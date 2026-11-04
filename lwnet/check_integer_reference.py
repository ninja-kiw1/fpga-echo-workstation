"""Boundary checks independent of neural-network accuracy."""
import json
from pathlib import Path
import numpy as np
import torch
from int8_reference import run_integer,self_test


def main():
    result={'base':self_test()}
    # 1x1 convolution acc=2*x-3, ReLU before negative BN coefficient.
    # q = ties_even((-3*max(acc,0)+5)/2), then signed saturation.
    plan=[dict(name='x',op='Input',inputs=[],scale=1,qmin=0,qmax=255),
          dict(name='y',op='ConvAffine',inputs=['x'],scale=1,qmin=-127,qmax=127,
               stride=[1,1],padding=[0,0],output_padding=[0,0],shift=1,relu_before_affine=True)]
    arrays={'y__weight':np.array([[[[2]]]],dtype='int8'), 'y__bias':np.array([-3],dtype='int32'),
            'y__multiplier':np.array([-3],dtype='int32'), 'y__offset':np.array([5],dtype='int64')}
    x=torch.tensor([[[[0,1,2,3,255]]]],dtype=torch.uint8)
    actual,_=run_integer(plan,arrays,x)
    assert actual.flatten().tolist()==[2,2,1,-2,-127],actual
    result['fused']='PASS: ReLU before negative BN, signed half ties, INT8 saturation'
    # Sum before rounding; independently rounded branches would yield zero.
    plan=[dict(name='x',op='Input',inputs=[],scale=1,qmin=0,qmax=255),
          dict(name='a',op='ReLU',inputs=['x'],scale=1,qmin=-127,qmax=127),
          dict(name='b',op='ReLU',inputs=['x'],scale=1,qmin=-127,qmax=127),
          dict(name='y',op='Add',inputs=['a','b'],scale=2,qmin=-127,qmax=127,shift=1)]
    actual,_=run_integer(plan,{'y__multiplier':np.array([1,1],dtype='int32')},torch.ones((1,1,2,2),dtype=torch.uint8))
    assert actual.flatten().tolist()==[1]*4
    result['add']='PASS: single rounding after branch accumulation'
    # Pool selects maxima in each 2x2 spatial window without mixing channels.
    plan=[dict(name='x',op='Input',inputs=[],scale=1,qmin=0,qmax=255),
          dict(name='y',op='MaxPool2d',inputs=['x'],scale=1,qmin=-127,qmax=127)]
    actual,_=run_integer(plan,{},torch.tensor([[[[1,9,2,3],[4,5,8,7],[0,1,2,3],[6,5,4,10]]]],dtype=torch.uint8))
    assert actual.flatten().tolist()==[9,8,6,10]
    result['pool']='PASS: 2x2 spatial window maxima'
    result['status']='PASS'
    path=Path(__file__).resolve().parent/'results/stage1_int8_v2/arithmetic_checks.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
