import json
import os
import time
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
clock_cells = [canvas.create_rectangle(8+i*5,230,13+i*5,244,fill='#050505',outline='') for i in range(20)]
clock_counter = 0
entry_text = tk.StringVar()
entry = tk.Entry(root, width=60, font=('DejaVu Sans',18), textvariable=entry_text)
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
events = []
def geometry(widget):
    return {'x':widget.winfo_rootx(), 'y':widget.winfo_rooty(),
            'width':widget.winfo_width(), 'height':widget.winfo_height(),
            'visible':bool(widget.winfo_viewable())}
def publish():
    state = {'monotonic':time.monotonic(), 'text':entry.get(), 'count':count,
             'focus':str(root.focus_get()),
             'marker':{'x':canvas.winfo_rootx()+8, 'y':canvas.winfo_rooty()+230,
                       'counter':clock_counter, 'cell_width':5, 'height':14},
             'controls':{name:geometry(widget) for name,widget in
                         [('root',root),('canvas',canvas),('entry',entry),('button',button)]},
             'events':events[-50:]}
    temporary = Path('/proofs/fixture-state.tmp')
    temporary.write_text(json.dumps(state), encoding='utf-8')
    temporary.replace('/proofs/fixture-state.json')
    if all(state['controls'][name]['visible'] and state['controls'][name]['width']>10
           for name in ('canvas','entry','button')):
        Path('/proofs/fixture-ready').touch()
def observe(event):
    # Observe actual X11 events delivered to these test widgets. No host-side
    # input is injected, and only synthetic fixture text is recorded.
    events.append({'type':str(event.type), 'widget':str(event.widget),
                   'x':event.x_root, 'y':event.y_root, 'monotonic':time.monotonic()})
    del events[:-50]
    root.after_idle(publish)
for event in ('<ButtonPress-1>', '<ButtonRelease-1>'):
    root.bind_all(event, observe, add='+')
entry_text.trace_add('write', lambda *_:root.after_idle(publish))
def tick():
    global clock_counter
    clock_counter = int(time.monotonic()*2) & 65535
    bits = '1010' + format(clock_counter,'016b')
    for cell, bit in zip(clock_cells,bits):
        canvas.itemconfigure(cell,fill='#fafafa' if bit=='1' else '#050505')
    publish()
    root.after(100, tick)
root.after_idle(tick)
Path('/proofs/input.json').write_text(json.dumps({'count':0,'text':''}), encoding='utf-8')
root.mainloop()
