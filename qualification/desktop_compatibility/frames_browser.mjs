// Decode the exact captured native frame bytes, then compare every RGB pixel
// with the independent native qualification decoder. No synthetic frame source.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import path from 'node:path';
import {chromium, firefox, webkit} from 'playwright';

const config = JSON.parse(process.argv[2]);
const receipt = JSON.parse(await readFile(config.receipt, 'utf8'));
assert.equal(receipt.passed, true, 'Native application qualification must pass first');
assert(receipt.frames.length > 0);
const files = new Map();
for (const frame of receipt.frames) {
  assert.equal(frame.encoding, 'png');
  const name = `${frame.stage}-${frame.sequence}.png`;
  const bytes = await readFile(path.join(config.directory, name));
  assert.equal(bytes.length, frame.encoded_bytes);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), frame.sha256,
    'Browser evidence must retain the original encoded bytes');
  files.set(`/${name}`, bytes);
}
const server = createServer((request, response) => {
  if (request.url === '/') {
    response.setHeader('Content-Type', 'text/html');
    response.end('<!doctype html><style>body{margin:0}canvas{display:block}</style><canvas></canvas>');
  } else if (files.has(request.url)) {
    response.setHeader('Content-Type', 'image/png');
    response.end(files.get(request.url));
  } else {
    response.writeHead(404).end();
  }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const results = [];
try {
  await mkdir(config.output, {recursive: true});
  for (const [name, engine] of Object.entries({chromium, firefox, webkit})) {
    const browser = await engine.launch({headless: true, ...(name === 'chromium' ? {chromiumSandbox: true} : {})});
    try {
      const page = await browser.newPage({viewport: {width: 1100, height: 800}});
      await page.goto(`http://127.0.0.1:${server.address().port}/`);
      for (const frame of receipt.frames) {
        const actual = await page.evaluate(async file => {
          const image = new Image();
          image.src = file;
          await image.decode();
          const canvas = document.querySelector('canvas');
          canvas.width = image.naturalWidth;
          canvas.height = image.naturalHeight;
          const context = canvas.getContext('2d');
          context.drawImage(image, 0, 0);
          const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
          const rgb = new Uint8Array(canvas.width * canvas.height * 3);
          for (let i = 0, j = 0; i < rgba.length; i += 4, j += 3) {
            if (rgba[i + 3] !== 255) throw Error('Native opaque frame became transparent');
            rgb.set(rgba.subarray(i, i + 3), j);
          }
          const hash = await crypto.subtle.digest('SHA-256', rgb);
          return {width: canvas.width, height: canvas.height,
            sha256: Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('')};
        }, `/${frame.stage}-${frame.sequence}.png`);
        assert.deepEqual(actual, {width: frame.width, height: frame.height, sha256: frame.decoded_sha256});
        results.push({browser: name, version: browser.version(), stage: frame.stage, sequence: frame.sequence, ...actual});
      }
      await page.screenshot({path: path.join(config.output, `${name}.png`)});
    } finally {
      await browser.close();
    }
  }
  await writeFile(path.join(config.output, 'frames.json'), JSON.stringify({passed: true, results}, null, 2) + '\n');
  console.log(`PASS: ${results.length} actual native frames decoded with matching pixels in three browsers`);
} finally {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
