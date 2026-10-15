// ui_drive scenario: connect the real app to the local Garry's Mod test server
// (started by gmod_fixture.py) and press every built-in button, checking the
// exact text each reply puts in the console. Needs the server installed by
// the live test runner (`python -m tools.live_test.run run gmod`).
//
// Replies are checked character by character: a stray control character (for
// example a leftover end-marker packet) shows as a box in the console and
// fails the check.

export default async function gmodConsole(helpers) {
  const { click, type, evaluate, waitFor, check, screenshot, fixture } = helpers;
  const settle = (id) => waitFor(
    `document.getElementById('${id}').classList.contains('show') && ` +
    `document.querySelector('#${id} .modal').getAnimations().length === 0`, 5000);
  // Every console line, with any control character shown as <code>.
  const lines = () => evaluate(
    "Array.from(document.querySelectorAll('#console .ln .msg')).map(m => " +
    "Array.from(m.textContent).map(c => c.charCodeAt(0) < 32 && c !== '\\t' ? '<' + c.charCodeAt(0) + '>' : c).join(''))");
  // The page disables the card's buttons while a command runs.
  const idle = () => waitFor("!document.querySelector('[data-card] [data-qbtn]').disabled", 10000).catch(() => {});

  // Presses a built-in button (typing its value when it asks for one) and
  // returns the console lines its reply added.
  const press = async (label, value) => {
    const index = await evaluate(
      `Array.from(document.querySelectorAll('[data-card] [data-qbtn]')).findIndex(b => b.textContent === ${JSON.stringify(label)})`);
    const before = (await lines()).length;
    await click(`[data-card] [data-qbtn="${index}"]`);
    if (value !== undefined) {
      await settle("prompt");
      await type("#promptInput", value);
      await click("#promptYes");
    }
    await waitFor(`document.querySelectorAll('#console .ln').length >= ${before} + 2`, 10000).catch(() => {});
    await new Promise(r => setTimeout(r, 1000));
    await idle();
    const added = (await lines()).slice(before);
    return added[0] === `$ ${label}` ? added.slice(1) : added;
  };

  await waitFor("window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_config === 'function'", 10000);

  await click("#addBtn");
  await settle("modal");
  await evaluate("(() => { const s = document.getElementById('fGame'); s.value = 'gmod'; s.dispatchEvent(new Event('change', {bubbles: true})); })()");
  await type("#fHost", fixture.host);
  await type("#fPort", String(fixture.port));
  await click("#saveBtn");
  await waitFor("document.querySelectorAll('[data-card]').length === 1 && !document.getElementById('modal').classList.contains('show')", 5000);

  await type("[data-card] [data-pw]", fixture.password);
  await click("[data-card] [data-connect]");
  const connected = await waitFor("document.querySelector('[data-card]').classList.contains('connected')", 15000)
    .then(() => true).catch(() => false);
  check("connected to the Garry's Mod server", connected, JSON.stringify(await lines()));

  const status1 = await press("Status");
  check("Status: first reply starts with the hostname line", /^hostname: /.test(status1[0] || ""), JSON.stringify(status1));
  check("Status: names the starting map", status1.some(l => /^map\s+: gm_construct /.test(l)), JSON.stringify(status1));

  const status2 = await press("Status");
  check("Status: a second reply on the same connection is clean", /^hostname: /.test(status2[0] || ""), JSON.stringify(status2));

  const say = await press("Say", "SRT app check");
  check("Say: reply is the chat line", JSON.stringify(say) === JSON.stringify(["Console: SRT app check"]), JSON.stringify(say));

  // With no such player online the server answers that the name is not found.
  const kick = await press("Kick", "SRTTestPlayer");
  check("Kick: reply is the server's not-found line", JSON.stringify(kick) === JSON.stringify(['name "SRTTestPlayer" not found']), JSON.stringify(kick));

  const change = await press("Change Level", "gm_flatgrass");
  check("Change Level: an empty reply shows (no output)", JSON.stringify(change) === JSON.stringify(["(no output)"]), JSON.stringify(change));

  // The map change itself, through the app.
  await new Promise(r => setTimeout(r, 3000));
  const status3 = await press("Status");
  check("Status: names the new map after Change Level", status3.some(l => /^map\s+: gm_flatgrass /.test(l)), JSON.stringify(status3));

  const all = await lines();
  const marked = all.filter(l => /<\d+>/.test(l));
  check("no control characters anywhere in the console", marked.length === 0, JSON.stringify(marked));
  const errors = await evaluate("Array.from(document.querySelectorAll('#console .ln.err .msg')).map(m => m.textContent)");
  check("no error lines in the console", errors.length === 0, JSON.stringify(errors));
  await screenshot("console");
}
