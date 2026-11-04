import glob, sys, numpy as np, cv2
sys.path.insert(0, 'E:/PLD/keyframe')
import echo_core
D = 'E:/PLD/HMC-QU Dataset-Kaggle v1.0 (1)/HMC-QU Dataset-Kaggle v1.0/HMC-QU/A4C/'
vs = sorted(glob.glob(D + '*.avi'))
tiles = []
for f in vs[::8]:
    c = cv2.VideoCapture(f); fr = []
    while True:
        ok, x = c.read()
        if not ok: break
        fr.append(cv2.cvtColor(x, cv2.COLOR_BGR2GRAY))
    fr = np.stack(fr)
    b = echo_core.lc_detect(fr, 8)
    im = cv2.cvtColor(fr[0], cv2.COLOR_GRAY2BGR)
    if b:
        cv2.rectangle(im, (b.x, b.y), (b.x + b.side, b.y + b.side), (0, 0, 255), 3)
        cv2.rectangle(im, (b.bx, b.by), (b.bx + b.bw, b.by + b.bh), (0, 255, 0), 2)
    print(f.split('/')[-1], fr.shape, b and (b.x, b.y, b.side, b.bx, b.by, b.bw, b.bh))
    tiles.append(cv2.resize(im, (318, 211)))
while len(tiles) % 5: tiles.append(np.zeros_like(tiles[0]))
rows = [np.hstack(tiles[i:i + 5]) for i in range(0, len(tiles), 5)]
cv2.imwrite('results/probe_crop.png', np.vstack(rows))
