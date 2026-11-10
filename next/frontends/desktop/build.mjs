import {copyFile,mkdir,readFile,writeFile} from 'node:fs/promises';
const source = name => new URL(name, import.meta.url);
const output = name => source(`./dist/${name}`);
await mkdir(output(''), {recursive:true});
const capture = await readFile(source('capture-panel.html'), 'utf8');
for (const name of ['index.html', 'capture.html']) {
  const html = await readFile(source(name), 'utf8');
  const marker = '<!-- capture-panel -->';
  if (html.split(marker).length !== 2) throw Error(`Expected one capture panel in ${name}`);
  await writeFile(output(name), html.replace(marker, () => capture));
}
for (const name of ['fixture.html', 'viewer.html', 'style.css', 'fixture.css']) {
  await copyFile(source(name), output(name));
}
await copyFile(source('../../../branding/icon-256.png'), output('mixel-icon.png'));
