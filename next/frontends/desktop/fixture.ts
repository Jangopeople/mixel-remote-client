(()=>{let count=0;setInterval(()=>{document.querySelector<HTMLElement>('#clock')!.textContent=`Live native frame source ${++count} · ${new Date().toLocaleTimeString()}`;},100);})();
