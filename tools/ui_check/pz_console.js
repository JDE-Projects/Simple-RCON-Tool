// ui_drive scenario: connect the real app to the local Project Zomboid test
// server (started by pz_fixture.py) and press every built-in button, checking
// the exact text each reply puts in the console. Needs the server installed by
// the live test runner (`python -m tools.live_test.run run pz`).
//
// Replies are checked character by character: a stray control character (for
// example a leftover end-marker packet) shows as a box in the console and
// fails the check.

export default async function pzConsole(helpers) {
  const { click, type, evaluate, waitFor, check, screenshot, fixture } = helpers;
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const settle = (id) => waitFor(
    `document.getElementById('${id}').classList.contains('show') && ` +
    `document.querySelector('#${id} .modal').getAnimations().length === 0`, 5000);
  // Every console line, with any control character shown as <code>.
  const lines = () => evaluate(
    "Array.from(document.querySelectorAll('#console .ln .msg')).map(m => " +
    "Array.from(m.textContent).map(c => c.charCodeAt(0) < 32 && c !== '\\t' ? '<' + c.charCodeAt(0) + '>' : c).join(''))");
  // The page disables the card's buttons while a command runs. A long reply such
  // as Show Options can take well over 10 s to reach the page line by line.
  const idle = () => waitFor("!document.querySelector('[data-card] [data-qbtn]').disabled", 30000)
    .then(() => true).catch(() => false);

  // Presses a built-in button (confirming it and typing its value when it asks)
  // and returns the console lines its reply added.
  const press = async (label, value, confirm = false) => {
    const index = await evaluate(
      `Array.from(document.querySelectorAll('[data-card] [data-qbtn]')).findIndex(b => b.textContent === ${JSON.stringify(label)})`);
    const before = (await lines()).length;
    await click(`[data-card] [data-qbtn="${index}"]`);
    if (confirm) {
      await settle("confirm");
      await click("#confirmYes");
    }
    if (value !== undefined) {
      await settle("prompt");
      await type("#promptInput", value);
      await click("#promptYes");
    }
    await waitFor(`document.querySelectorAll('#console .ln').length >= ${before} + 2`, 10000).catch(() => {});
    await sleep(1000);
    check(`${label}: buttons unlock after the reply`, await idle());
    const added = (await lines()).slice(before);
    return added[0] === `$ ${label}` ? added.slice(1) : added;
  };

  await waitFor("window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_config === 'function'", 10000);

  await click("#addBtn");
  await settle("modal");
  await evaluate("(() => { const s = document.getElementById('fGame'); s.value = 'pz'; s.dispatchEvent(new Event('change', {bubbles: true})); })()");
  await type("#fHost", fixture.host);
  await type("#fPort", String(fixture.port));
  await click("#saveBtn");
  await waitFor("document.querySelectorAll('[data-card]').length === 1 && !document.getElementById('modal').classList.contains('show')", 5000);

  // The server opens RCON about a minute after the fixture starts it, so Connect
  // is retried until it answers. Lines from failed attempts are not judged.
  let connected = false;
  for (let attempt = 0; attempt < 48 && !connected; attempt++) {
    await evaluate("document.querySelector('[data-card] [data-pw]').value = ''");
    await type("[data-card] [data-pw]", fixture.password);
    await click("[data-card] [data-connect]");
    connected = await waitFor("document.querySelector('[data-card]').classList.contains('connected')", 5000)
      .then(() => true).catch(() => false);
    if (!connected) await sleep(5000);
  }
  check("connected to the Project Zomboid server", connected, JSON.stringify(await lines()));
  const start = (await lines()).length;

  const players1 = await press("Players");
  check("Players: reply is the connected-players line", JSON.stringify(players1) === JSON.stringify(["Players connected (0):"]), JSON.stringify(players1));

  const players2 = await press("Players");
  check("Players: a second reply on the same connection is clean", JSON.stringify(players2) === JSON.stringify(["Players connected (0):"]), JSON.stringify(players2));

  const save = await press("Save");
  check("Save: reply is the server's saved line", JSON.stringify(save) === JSON.stringify(["World saved"]), JSON.stringify(save));

  // The button adds the quotes, so plain text goes through.
  const message = await press("Server Message", "SRT app check");
  check("Server Message: reply is the sent line", JSON.stringify(message) === JSON.stringify(["Message sent."]), JSON.stringify(message));

  // With no such player online the server answers that the user does not exist.
  const kick = await press("Kick User", '"SRTTestPlayer"');
  check("Kick User: reply is the server's not-found line", JSON.stringify(kick) === JSON.stringify(["User SRTTestPlayer doesn't exist."]), JSON.stringify(kick));

  const ban = await press("Ban User", '"SRTTestPlayer"', true);
  check("Ban User: reply is the server's banned line", JSON.stringify(ban) === JSON.stringify(["System banned user SRTTestPlayer"]), JSON.stringify(ban));

  const unban = await press("Unban User", '"SRTTestPlayer"');
  check("Unban User: reply is the server's unbanned line", JSON.stringify(unban) === JSON.stringify(["System unbanned user SRTTestPlayer"]), JSON.stringify(unban));

  // A long reply: every public setting, one per line, the welcome message last.
  const options = await press("Show Options");
  check("Show Options: starts with the heading and the first setting",
    options[0] === "List of Server Options:" && options[1] === "* AdminSafehouse=false", JSON.stringify(options.slice(0, 3)));
  check("Show Options: ends with the welcome message", /^\* ServerWelcomeMessage=/.test(options[options.length - 1] || ""), JSON.stringify(options.slice(-2)));

  const quit = await press("Save & Quit", undefined, true);
  check("Save & Quit: reply starts with the server's Quit line", quit[0] === "Quit", JSON.stringify(quit));

  const all = (await lines()).slice(start);
  const marked = all.filter(l => /<\d+>/.test(l));
  check("no control characters anywhere in the console", marked.length === 0, JSON.stringify(marked));
  const errors = await evaluate(
    `Array.from(document.querySelectorAll('#console .ln')).slice(${start}).filter(l => l.classList.contains('err')).map(l => l.textContent)`);
  check("no error lines in the console once connected", errors.length === 0, JSON.stringify(errors));
  await screenshot("console");
}
