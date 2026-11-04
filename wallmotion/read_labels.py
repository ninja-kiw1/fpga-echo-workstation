"""HMC-QU A4C.xlsx -> results/labels_a4c.json (run with the anaconda base python, which has openpyxl).
Segment columns in the sheet: SEG3 basal inferoseptal, SEG9 mid inferoseptal, SEG14 apical septal,
SEG16 apical lateral, SEG12 mid anterolateral, SEG6 basal anterolateral; MI = 1, non-MI = 0.
"""
import json
import os
import pandas as pd

D = r'E:/PLD/HMC-QU Dataset-Kaggle v1.0 (1)/HMC-QU Dataset-Kaggle v1.0'
x = pd.read_excel(os.path.join(D, 'A4C.xlsx'), header=None)
segs = [str(s) for s in x.iloc[1, 1:7]]
assert segs == ['SEG3', 'SEG9', 'SEG14', 'SEG16', 'SEG12', 'SEG6'], segs
rows = []
for i in range(2, len(x)):
    name = x.iloc[i, 0]
    if not isinstance(name, str):
        continue
    lab = [str(v).strip() for v in x.iloc[i, 1:7]]
    assert all(v in ('MI', 'non-MI') for v in lab), (name, lab)
    rows.append(dict(name=name.strip(), seg=[1 if v == 'MI' else 0 for v in lab],
                     ref=int(x.iloc[i, 7]), end=int(x.iloc[i, 8]), has_mask=str(x.iloc[i, 9]).strip() == '\u00fc'))
vids = set(os.path.splitext(f)[0] for f in os.listdir(os.path.join(D, 'HMC-QU', 'A4C')) if f.endswith('.avi'))
miss = [r['name'] for r in rows if r['name'] not in vids]
print(len(rows), 'rows;', len(vids), 'videos; missing', miss, '; extra', sorted(vids - {r['name'] for r in rows}))
print('MI recordings', sum(any(r['seg']) for r in rows), 'masks', sum(r['has_mask'] for r in rows),
      'MI segments per position', [sum(r['seg'][k] for r in rows) for k in range(6)])
os.makedirs('results', exist_ok=True)
json.dump(dict(segments=segs, rows=rows), open('results/labels_a4c.json', 'w'), indent=1)
