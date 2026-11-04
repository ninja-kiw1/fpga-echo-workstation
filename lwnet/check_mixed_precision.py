"""Independent INT16 activation / INT8 weight boundary tests."""
import json
from pathlib import Path
import tempfile
import numpy as np
import torch
from int8_reference import conv_integer,run_integer


def main():
    spec=dict(op='Conv2d',stride=[1,1],padding=[1,1],output_padding=[0,0])
    x=torch.full((1,2,3,3),32767,dtype=torch.int64)
    w=torch.full((1,2,3,3),127,dtype=torch.int64)
    result=conv_integer(x,w,spec)
    expected=torch.tensor([[[[8,12,8],[12,18,12],[8,12,8]]]],dtype=torch.int64)*32767*127
    assert torch.equal(result,expected)
    assert torch.equal(conv_integer(-x,w,spec),-expected)
    # Produce a true INT16 tensor exceeding 127, and verify it survives export.
    plan=[dict(name='x',op='Input',inputs=[],scale=1,qmin=0,qmax=255),
          dict(name='y',op='Conv2d',inputs=['x'],scale=1,qmin=-32767,qmax=32767,
               stride=[1,1],padding=[0,0],output_padding=[0,0],shift=0)]
    arrays={'y__weight':np.array([[[[127]]]],dtype='int8'),
            'y__bias':np.array([1000],dtype='int32'),'y__multiplier':np.array([2],dtype='int32')}
    outdir=Path(__file__).resolve().parent/'results/quant_sensitivity'
    outdir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=outdir) as tmp:
        result,_=run_integer(plan,arrays,torch.tensor([[[[0,1,255]]]],dtype=torch.uint8),dump_dir=Path(tmp))
        assert result.flatten().tolist()==[2000,2254,32767]
        saved=np.load(Path(tmp)/'y.npy',allow_pickle=False)
        assert saved.dtype==np.int16 and saved.flatten().tolist()==[2000,2254,32767]
    report=dict(status='PASS',checks=['INT16 extremes times INT8 weights with padded analytic sums',
                                    'negative input symmetry','INT16 saturation and golden tensor export without narrowing'])
    (outdir/'mixed_arithmetic_checks.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
