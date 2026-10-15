// ui_drive scenario: connect the real app to the local ARK: Survival Ascended
// test server (started by ark_asa_fixture.py) and press every built-in button,
// checking the exact text each reply puts in the console. Needs the server
// installed by the live test runner (`python -m tools.live_test.run run ark_asa`).
//
// Replies are checked character by character: a stray control character shows
// as a box in the console and fails the check.

export default async function arkAsaConsole(helpers) {
  const { click, type, evaluate, waitFor, check, screenshot, fixture } = helpers;
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const settle = (id) => waitFor(
    `document.getElementById('${id}').classList.contains('show') && ` +
    `document.querySelector('#${id} .modal').getAnimations().length === 0`, 5000);
  // Every console line, with any control character shown as <code>.
  const lines = () => evaluate(
    "Array.from(document.querySelectorAll('#console .ln .msg')).map(m => " +
    "Array.from(m.textContent).map(c => c.charCodeAt(0) < 32 && c !== '\\t' ? '<' + c.charCodeAt(0) + '>' : c).join(''))");
  // The page disables the card's buttons while a command runs.
  const idle = () => waitFor("!document.querySelector('[data-card] [data-qbtn]').disabled", 10000).catch(() => {});

  // Presses a built-in button (confirming it and typing its value when it asks)
  // and returns the console lines its reply added.
  const press = async (label, value, confirm = false) => {
    const index = await evaluate(
      `Array.from(document.querySelectorAll('[data-card] [data-qbtn]')).findIndex(b => b.textContent === ${JSON.stringify(label)})`);
    const before = (await lines()).length;
    const started = Date.now();
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
    await waitFor(`document.querySelectorAll('#console .ln').length >= ${before} + 2`, 20000).catch(() => {});
    tookMs[label] = Date.now() - started;
    await sleep(1000);
    await idle();
    const added = (await lines()).slice(before);
    return added[0] === `$ ${label}` ? added.slice(1) : added;
  };
  // How long each button's reply took to appear, reported with every failure.
  const tookMs = {};
  const same = (got, want) => JSON.stringify(got) === JSON.stringify(want);
  // What the server answers for any command with no output of its own.
  const noResponse = ["Server received, But no response!!"];

  await waitFor("window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_config === 'function'", 10000);

  await click("#addBtn");
  await settle("modal");
  await evaluate("(() => { const s = document.getElementById('fGame'); s.value = 'ark_asa'; s.dispatchEvent(new Event('change', {bubbles: true})); })()");
  await type("#fHost", fixture.host);
  await type("#fPort", String(fixture.port));
  await click("#saveBtn");
  await waitFor("document.querySelectorAll('[data-card]').length === 1 && !document.getElementById('modal').classList.contains('show')", 5000);

  // The server opens RCON about two minutes after the fixture starts it, so Connect
  // is retried until it answers. While the map loads the port takes the connection
  // but never answers the login, so each attempt waits out the app's 8 s timeout.
  // Lines from failed attempts are not judged.
  let connected = false;
  for (let attempt = 0; attempt < 25 && !connected; attempt++) {
    await evaluate("document.querySelector('[data-card] [data-pw]').value = ''");
    await type("[data-card] [data-pw]", fixture.password);
    await click("[data-card] [data-connect]");
    connected = await waitFor("document.querySelector('[data-card]').classList.contains('connected')", 12000)
      .then(() => true).catch(() => false);
    if (!connected) await sleep(2000);
  }
  check("connected to the ARK: Survival Ascended server", connected, JSON.stringify(await lines()));
  const start = (await lines()).length;

  const players1 = await press("List Players");
  check("List Players: reply is the no-players line", same(players1, ["No Players Connected"]), JSON.stringify(players1) + ` after ${tookMs["List Players"]} ms`);

  const players2 = await press("List Players");
  check("List Players: a second reply on the same connection is clean", same(players2, ["No Players Connected"]), JSON.stringify(players2));

  const save = await press("Save World");
  check("Save World: reply is the server's saved line", same(save, ["World Saved"]), JSON.stringify(save));

  const message = await press("Server Message", "SRT app check");
  check("Server Message: reply is the no-output line", same(message, noResponse), JSON.stringify(message));

  // GetChat returns the chat since the last GetChat: the message just sent.
  const chat = await press("Get Chat");
  check("Get Chat: reply is the message just sent", same(chat, ["SERVER: SRT app check"]), JSON.stringify(chat));

  const broadcast = await press("Broadcast", "SRT app check");
  check("Broadcast: reply is the no-output line", same(broadcast, noResponse), JSON.stringify(broadcast));

  const dinos = await press("Destroy Wild Dinos", undefined, true);
  check("Destroy Wild Dinos: reply is the server's destroyed line", same(dinos, ["All Wild Dinos Destroyed"]), JSON.stringify(dinos));

  // The server answers "<arg> Kicked" for any argument, whether or not anyone matches.
  const kick = await press("Kick Player", "0002aaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  check("Kick Player: reply is the server's kicked line", same(kick, ["0002aaaaaaaaaaaaaaaaaaaaaaaaaaaa Kicked"]), JSON.stringify(kick));

  // Ban and unban work on IDs that are not online.
  const ban = await press("Ban Player", "0002aaaaaaaaaaaaaaaaaaaaaaaaaaaa", true);
  check("Ban Player: reply is the server's banned line", same(ban, ["0002aaaaaaaaaaaaaaaaaaaaaaaaaaaa Banned"]), JSON.stringify(ban));

  const unban = await press("Unban Player", "0002aaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  check("Unban Player: reply is the server's unbanned line", same(unban, ["0002aaaaaaaaaaaaaaaaaaaaaaaaaaaa Unbanned"]), JSON.stringify(unban));

  const quit = await press("Shutdown", undefined, true);
  check("Shutdown: reply starts with the server's exiting line", quit[0] === "Exiting...", JSON.stringify(quit) + ` after ${tookMs["Shutdown"]} ms`);

  const all = (await lines()).slice(start);
  const marked = all.filter(l => /<\d+>/.test(l));
  check("no control characters anywhere in the console", marked.length === 0, JSON.stringify(marked));
  const errors = await evaluate(
    `Array.from(document.querySelectorAll('#console .ln')).slice(${start}).filter(l => l.classList.contains('err')).map(l => l.textContent)`);
  check("no error lines in the console once connected", errors.length === 0, JSON.stringify(errors));
  await screenshot("console");
}
