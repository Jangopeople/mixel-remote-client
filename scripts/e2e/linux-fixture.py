import json
import os
from pathlib import Path
import tkinter as tk
root = tk.Tk()
root.title('Mixel isolated customer desktop')
root.geometry('1000x700+40+40')
root.configure(background='#fafafa')
label = tk.Label(root, text='MIXEL ISOLATED SUPPORT TEST — NO CUSTOMER DATA', font=('DejaVu Sans', 19), bg='#fafafa')
label.pack(pady=16)
canvas = tk.Canvas(root, width=900, height=250, highlightthickness=0)
canvas.pack()
for left, color, name in [(0,'#e51d36','RED'),(300,'#13b76c','GREEN'),(600,'#176ee9','BLUE')]:
    canvas.create_rectangle(left,0,left+300,250,fill=color,outline=color)
    canvas.create_text(left+150,125,text=name,fill='white',font=('DejaVu Sans',30,'bold'))
entry = tk.Entry(root, width=60, font=('DejaVu Sans',18))
entry.pack(pady=18)
count = 0
state_label = tk.Label(root, text='REMOTE INPUT COUNT: 0', font=('DejaVu Sans',18), bg='#fafafa')
state_label.pack(pady=8)
def click():
    global count
    count += 1
    text = entry.get()
    state_label.config(text=f'REMOTE INPUT COUNT: {count} | {text}')
    Path('/proofs/input.json').write_text(json.dumps({'count':count,'text':text}), encoding='utf-8')
button = tk.Button(root,text='REMOTE CLICK PROOF',command=click,font=('DejaVu Sans',20),width=27,height=2)
button.pack(pady=8)
Path('/proofs/input.json').write_text(json.dumps({'count':0,'text':''}), encoding='utf-8')
Path('/proofs/fixture-ready').touch()
root.mainloop()
