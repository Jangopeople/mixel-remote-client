#!/bin/bash
# fps.sh <name>: play the motion test on Win10 and read the technician's frame rate display three times
cd "$(dirname "$0")"
(./itask.sh qa-win10 22 anim.ps1 > /dev/null &); sleep 8
for i in 1 2 3; do ./w11.sh $1$i >/dev/null; sleep 2; done
python3 -c "
from PIL import Image
out=Image.new('RGB',(900,360))
for i in (1,2,3):
    im=Image.open('shots/$1%d.png'%i); w,h=im.size
    # the metrics text sits at the right end of the status row under the picture
    out.paste(im.crop((900,560,1230,650)).resize((900,245)).crop((0,60,900,180)),(0,(i-1)*120))
out.save('shots/$1.png')"
echo shots/$1.png
