// Runs the JavaScript of one n8n Code node outside n8n.
// stdin: { code, items, upstream: { nodeName: items }, hour }   stdout: the returned items as JSON
// Only the globals the library's Code nodes use are provided.
let raw = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) raw += chunk;
const { code, items = [], upstream = {}, hour = 10 } = JSON.parse(raw);

const wrap = (list) => ({ all: () => list, first: () => list[0], item: list[0] });
const $input = wrap(items);
const $ = (name) => {
  if (!(name in upstream)) throw new Error(`no upstream data for node "${name}"`);
  return wrap(upstream[name]);
};
const $now = { hour, toFormat: () => 'Mon Jan 7', toISO: () => new Date().toISOString() };

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const run = new AsyncFunction('$input', '$', '$now', code);
process.stdout.write(JSON.stringify(await run($input, $, $now)));
