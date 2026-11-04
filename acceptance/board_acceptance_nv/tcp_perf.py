"""Compare identical serial TCP requests with independent, existing integer references."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tcp_image/tcp_image_ui'))
import confidence
import imagelink
import proto

def main():
    tag = sys.argv[1]
    flags = proto.FLAG_STATS | proto.FLAG_ECHO_LOGIT | proto.FLAG_ECHO_MASK
    link = imagelink.ImageLink('192.168.1.254', 5060, timeout=30)
    samples = []
    try:
        for name, model, res, threshold in [('fundus512',0,512,-3),('skin128',1,128,1),('echo128',2,128,1)]:
            small = np.fromfile(HERE/'inputs'/f'{name}.rgb', np.uint8).reshape(res,res,3)
            ref = np.fromfile(HERE/'inputs'/f'{name}.i8', np.int8).reshape(res,res)
            k = 512 // res
            rgb = np.repeat(np.repeat(small,k,0),k,1).tobytes()
            for i in range(-2,10):
                imgs,times,rows,stats = link.round_trip(rgb,mode=5,flags=flags,
                    aiparam=proto.make_aiparam(0,threshold,res,model))
                assert stats and stats['model']==model and stats['ai_res']==res and stats['ai_us']>0, stats
                assert all(r==dict(rows_ok=512,crc_errors=0,seq_errors=0) for r in rows.values()),rows
                plane = np.frombuffer(confidence.logit_plane(imgs['logit']),np.uint8).reshape(512,512).astype(np.int16)-128
                assert np.array_equal(plane,np.repeat(np.repeat(ref,k,0),k,1)), (name,i,'logits')
                mask = np.frombuffer(imgs['mask'],np.uint8).reshape(512,512,3)
                assert np.array_equal(mask,np.repeat((plane>=threshold)[:,:,None]*255,3,2)), (name,i,'mask')
                if i>=0:
                    row=dict(name=name,model=model,res=res,sample=i,ai_ms=stats['ai_us']/1000,**times)
                    samples.append(row)
                    print('TCP_SAMPLE '+json.dumps(row),flush=True)
    finally:
        link.close()
    (HERE/f'{tag}_tcp_performance.json').write_text(json.dumps(dict(
        utc=datetime.now(timezone.utc).isoformat(),pass_=True,flags=flags,mode=5,warmup=2,samples=samples),indent=2),encoding='utf-8')
    print('TCP_PERF_PASS',flush=True)

if __name__=='__main__': main()
