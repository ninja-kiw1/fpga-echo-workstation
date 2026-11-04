"""Explicit integer arithmetic reference, CPU only; not a native INT8 backend.

Conv sums use float64 on integer operands with checked exact-integer bounds.
All requantization uses int64 multiply and specified ties-to-even shifts.
"""
import math
import operator

import numpy as np
import torch
import torch.nn.functional as F


def round_shift(x, shift):
    """Signed round-to-nearest, ties-to-even. No implementation-defined shifts."""
    if shift == 0:
        return x
    a = x.abs()
    quotient = a >> shift
    remainder = a & ((1 << shift) - 1)
    half = 1 << (shift - 1)
    increment = (remainder > half) | ((remainder == half) & ((quotient & 1) != 0))
    return (quotient + increment.to(torch.int64)) * x.sign()


def fixed_coefficients(coefficients, offset=None):
    coefficients = torch.as_tensor(coefficients, dtype=torch.float64)
    max_value = float(coefficients.abs().max())
    shift = min(30, int(math.floor(math.log2((2**31-1)/max_value)))) if max_value else 30
    if shift < 0:
        raise ValueError('Coefficient cannot fit supported signed INT32 multiplier')
    m = torch.round(coefficients * (1 << shift)).to(torch.int64)
    assert int(m.abs().max()) < 2**31
    b = torch.zeros_like(m) if offset is None else torch.round(torch.as_tensor(offset,dtype=torch.float64)*(1 << shift)).to(torch.int64)
    return m, b, shift


def conv_integer(x, weight, spec):
    # An absolute-sum bound, including every term, guarantees exact float64 sums.
    transpose = spec['op'] == 'ConvTranspose2d'
    axis = (0,2,3) if transpose else (1,2,3)
    bound = int(x.abs().max()) * int(weight.abs().sum(dim=axis).max())
    if bound >= 2**53:
        raise OverflowError('Convolution exceeds exact binary64 integer range')
    if transpose:
        y = F.conv_transpose2d(x.double(), weight.double(), stride=spec['stride'],
                              padding=spec['padding'], output_padding=spec['output_padding'])
    else:
        y = F.conv2d(x.double(), weight.double(), stride=spec['stride'],padding=spec['padding'])
    if not torch.equal(y, y.round()):
        raise AssertionError('Nonintegral convolution sum')
    return y.to(torch.int64)


class Observer(torch.fx.Interpreter):
    def __init__(self, graph, capture=False):
        super().__init__(graph)
        self.ranges = {}
        self.tensors = {} if capture else None

    def run_node(self, node):
        y = super().run_node(node)
        if isinstance(y, torch.Tensor) and node.op != 'output':
            value = float(y.abs().max())
            self.ranges[node.name] = max(value, self.ranges.get(node.name, 0.0))
            if self.tensors is not None:
                self.tensors[node.name] = y.detach().clone()
        return y


def build_plan(graph, ranges, activation_bits=None):
    activation_bits = activation_bits or {}
    plan, arrays, scales = [], {}, {}
    for node in graph.graph.nodes:
        if node.op == 'output':
            continue
        name = node.name
        ins = [n.name for n in node.all_input_nodes]
        bits = activation_bits.get(name,8)
        assert bits in (8,16)
        limit = (1 << (bits-1))-1
        scale = max(ranges[name]/limit, 1e-10)
        spec = dict(name=name, inputs=ins, scale=scale, zero_point=0,
                    qmin=-limit,qmax=limit, calibration_absmax=ranges[name],bits=bits)
        if node.op == 'placeholder':
            spec.update(op='Input', scale=1/255, qmin=0, qmax=255)
        elif node.op == 'call_module':
            m = graph.get_submodule(node.target)
            spec.update(op=type(m).__name__, source_module=node.target)
            if isinstance(m,(torch.nn.Conv2d,torch.nn.ConvTranspose2d)):
                assert m.groups == 1 and m.dilation == (1,1) and m.padding_mode == 'zeros'
                w = m.weight.detach().double()
                axis = (0,2,3) if isinstance(m,torch.nn.ConvTranspose2d) else (1,2,3)
                ws = w.abs().amax(dim=axis).clamp_min(1e-12)/127
                view = (1,-1,1,1) if isinstance(m,torch.nn.ConvTranspose2d) else (-1,1,1,1)
                qw = torch.round(w/ws.reshape(view)).clamp(-127,127).to(torch.int64)
                bias = m.bias.detach().double() if m.bias is not None else torch.zeros(m.out_channels,dtype=torch.float64)
                qb = torch.round(bias/(scales[ins[0]]*ws)).to(torch.int64)
                previous = next(r for r in plan if r['name']==ins[0])
                bound = max(abs(previous['qmin']),abs(previous['qmax']))*qw.abs().sum(dim=axis)+qb.abs()
                assert int(bound.max()) < 2**31, 'INT32 accumulator bound failed'
                mul, off, shift = fixed_coefficients(scales[ins[0]]*ws/scale)
                assert int(bound.max())*int(mul.abs().max()) < 2**63
                arrays[name+'__weight'] = qw.numpy().astype('int8')
                arrays[name+'__bias'] = qb.numpy().astype('<i4')
                arrays[name+'__weight_scale'] = ws.numpy()
                arrays[name+'__multiplier'] = mul.numpy().astype('<i4')
                spec.update(stride=list(m.stride),padding=list(m.padding),kernel=list(m.kernel_size),
                            output_padding=list(m.output_padding) if isinstance(m,torch.nn.ConvTranspose2d) else [0,0],
                            groups=1, shift=shift, accumulator_abs_bound=int(bound.max()),
                            weight_layout='IOHW' if isinstance(m,torch.nn.ConvTranspose2d) else 'OIHW')
            elif isinstance(m,torch.nn.BatchNorm2d):
                a = m.weight.detach().double()/torch.sqrt(m.running_var.double()+m.eps)
                b = m.bias.detach().double()-a*m.running_mean.double()
                mul, off, shift = fixed_coefficients(a*scales[ins[0]]/scale,b/scale)
                previous = next(r for r in plan if r['name']==ins[0])
                assert int(mul.abs().max())*max(abs(previous['qmin']),abs(previous['qmax']))+int(off.abs().max()) < 2**63
                arrays[name+'__multiplier'] = mul.numpy().astype('<i4')
                arrays[name+'__offset'] = off.numpy().astype('<i8')
                spec['shift'] = shift
            elif isinstance(m,(torch.nn.ReLU,torch.nn.MaxPool2d)):
                spec['scale'] = scales[ins[0]]
                previous = next(r for r in plan if r['name']==ins[0])
                spec.update(qmin=previous['qmin'],qmax=previous['qmax'],bits=previous['bits'])
                if isinstance(m,torch.nn.MaxPool2d):
                    assert m.kernel_size==m.stride==2 and m.padding==0 and not m.ceil_mode
                    spec.update(kernel=2,stride=2)
            else:
                raise NotImplementedError(type(m))
        elif node.op == 'call_function' and node.target in (operator.add,torch.cat):
            spec['op'] = 'Add' if node.target==operator.add else 'Concat'
            if node.target==torch.cat:
                assert node.kwargs['dim']==1
                spec['dim']=1
            mul,_,shift = fixed_coefficients([scales[n]/scale for n in ins])
            input_bound = max(max(abs(r['qmin']),abs(r['qmax'])) for r in plan if r['name'] in ins)
            assert int(mul.abs().sum())*input_bound < 2**63
            arrays[name+'__multiplier'] = mul.numpy().astype('<i4')
            spec['shift']=shift
        else:
            raise NotImplementedError(node)
        plan.append(spec)
        scales[name]=spec['scale']
    return plan, arrays


def run_integer(plan, arrays, rgb_uint8, float_tensors=None, dump_dir=None):
    env, stats = {}, []
    remaining={s['name']:sum(s['name'] in other['inputs'] for other in plan) for s in plan}
    output=None
    for s in plan:
        name,op=s['name'],s['op']
        ins=[env[n] for n in s['inputs']]
        def arr(suffix):
            return torch.from_numpy(arrays[name+'__'+suffix].astype('int64'))
        if op=='Input':
            raw=rgb_uint8.to(torch.int64)
        elif op in ('Conv2d','ConvTranspose2d','ConvAffine'):
            conv_spec={**s,'op':'Conv2d'} if op=='ConvAffine' else s
            acc=conv_integer(ins[0],arr('weight'),conv_spec)+arr('bias').reshape(1,-1,1,1)
            assert int(acc.abs().max())<2**31
            if op=='ConvAffine' and s['relu_before_affine']:
                acc=acc.clamp_min(0)
            v=acc*arr('multiplier').reshape(1,-1,1,1)
            if op=='ConvAffine':
                v=v+arr('offset').reshape(1,-1,1,1)
            raw=round_shift(v,s['shift'])
        elif op=='BatchNorm2d':
            v=ins[0]*arr('multiplier').reshape(1,-1,1,1)+arr('offset').reshape(1,-1,1,1)
            raw=round_shift(v,s['shift'])
        elif op=='ReLU':
            raw=ins[0].clamp_min(0)
        elif op=='MaxPool2d':
            n,c,h,w=ins[0].shape
            assert h%2==w%2==0
            raw=ins[0].reshape(n,c,h//2,2,w//2,2).amax(dim=(3,5))
        elif op=='Add':
            mult=arr('multiplier')
            raw=round_shift(sum(v*mult[i] for i,v in enumerate(ins)),s['shift'])
        elif op=='Concat':
            mult=arr('multiplier')
            raw=torch.cat([round_shift(v*mult[i],s['shift']) for i,v in enumerate(ins)],dim=1)
        else:
            raise NotImplementedError(op)
        clipped=(raw<s['qmin']) | (raw>s['qmax'])
        q=raw.clamp(s['qmin'],s['qmax'])
        row=dict(name=name,op=op,shape=list(q.shape),scale=s['scale'],
                 clipped_count=int(clipped.sum()),elements=q.numel(),qmin=int(q.min()),qmax=int(q.max()))
        if float_tensors is not None:
            diff=q.double()*s['scale']-float_tensors[name].double()
            row.update(mae=float(diff.abs().mean()),max_abs_error=float(diff.abs().max()))
        stats.append(row)
        if dump_dir:
            dtype='uint8' if op=='Input' else ('int16' if s['qmax']>127 else 'int8')
            np.save(dump_dir/(name+'.npy'),q.numpy().astype(dtype))
        env[name]=q
        for n in s['inputs']:
            remaining[n]-=1
            if remaining[n]==0:
                del env[n]
        output=q
    return output,stats


def fuse_conv_affine(graph, plan, arrays):
    """Preserve Conv->ReLU->BN ordering, omit intermediate INT8 roundings.

    This is a fused execution block, NOT moving BN through ReLU or conventional
    folding of BN into the weights. Shortcut Conv->BN is treated similarly.
    """
    consumers={s['name']:[r for r in plan if s['name'] in r['inputs']] for s in plan}
    scales={s['name']:s['scale'] for s in plan}
    skip=set(); replacements={}; new_arrays=dict(arrays)
    for conv in plan:
        if conv['op']!='Conv2d' or len(consumers[conv['name']])!=1:
            continue
        nxt=consumers[conv['name']][0]
        relu=nxt if nxt['op']=='ReLU' else None
        if relu:
            if len(consumers[relu['name']])!=1:
                continue
            bn=consumers[relu['name']][0]
        else:
            bn=nxt
        if bn['op']!='BatchNorm2d':
            continue
        m=graph.get_submodule(bn['source_module'])
        a=m.weight.detach().double()/torch.sqrt(m.running_var.double()+m.eps)
        b=m.bias.detach().double()-a*m.running_mean.double()
        ws=torch.from_numpy(arrays[conv['name']+'__weight_scale'])
        mul,offset,shift=fixed_coefficients(a*scales[conv['inputs'][0]]*ws/bn['scale'],b/bn['scale'])
        assert conv['accumulator_abs_bound']*int(mul.abs().max())+int(offset.abs().max())<2**63
        fused={**conv,'name':bn['name'],'op':'ConvAffine','scale':bn['scale'],
               'bits':bn['bits'],'qmin':bn['qmin'],'qmax':bn['qmax'],
               'calibration_absmax':bn['calibration_absmax'],'shift':shift,
               'relu_before_affine':relu is not None,'affine_source_module':bn['source_module'],
               'fused_nodes':[conv['name']]+([relu['name']] if relu else [])+[bn['name']]}
        replacements[bn['name']]=fused
        skip.add(conv['name'])
        if relu: skip.add(relu['name'])
        for suffix in ('weight','bias','weight_scale'):
            new_arrays[bn['name']+'__'+suffix]=arrays[conv['name']+'__'+suffix]
        new_arrays[bn['name']+'__multiplier']=mul.numpy().astype('<i4')
        new_arrays[bn['name']+'__offset']=offset.numpy().astype('<i8')
    new_plan=[replacements.get(s['name'],s) for s in plan if s['name'] not in skip]
    active={s['name'] for s in new_plan}
    new_arrays={k:v for k,v in new_arrays.items() if k.split('__')[0] in active}
    return new_plan,new_arrays


def self_test():
    # Independent Python integer ties-to-even oracle, including negative halfway cases.
    values=list(range(-65,66))+[-(2**40+3),2**40+3]
    for shift in (0,1,2,5,30):
        expected=[]
        for v in values:
            d=1<<shift
            a,r=divmod(abs(v),d)
            a+=int(2*r>d or (2*r==d and a%2==1))
            expected.append(a if v>=0 else -a)
        assert round_shift(torch.tensor(values),shift).tolist()==expected
    gen=torch.Generator().manual_seed(71)
    x=torch.randint(-127,128,(1,2,4,4),generator=gen,dtype=torch.int64)
    for transpose in (False,True):
        w=torch.randint(-127,128,(2,3,2,2) if transpose else (3,2,3,3),generator=gen,dtype=torch.int64)
        spec=dict(op='ConvTranspose2d' if transpose else 'Conv2d',stride=[2,2] if transpose else [1,1],
                  padding=[0,0] if transpose else [1,1],output_padding=[0,0])
        actual=conv_integer(x,w,spec)
        expected=torch.zeros((1,3,8,8) if transpose else (1,3,4,4),dtype=torch.int64)
        for co in range(3):
            for ci in range(2):
                for iy in range(4):
                    for ix in range(4):
                        for ky in range(w.shape[2]):
                            for kx in range(w.shape[3]):
                                if transpose:
                                    expected[0,co,iy*2+ky,ix*2+kx]+=int(x[0,ci,iy,ix])*int(w[ci,co,ky,kx])
                                else:
                                    sy,sx=iy+ky-1,ix+kx-1
                                    if 0<=sy<4 and 0<=sx<4:
                                        expected[0,co,iy,ix]+=int(x[0,ci,sy,sx])*int(w[co,ci,ky,kx])
        assert torch.equal(actual,expected)
    return 'PASS: signed ties-to-even shifts; conv padding and transposed conv against scalar INT64 loops'
