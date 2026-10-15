// ui_drive scenario: connect the real app to the local Squad test server
// (started by squad_fixture.py) and press every built-in button, checking the
// exact text each reply puts in the console. Needs the server installed by the
// live test runner (`python -m tools.live_test.run run squad`).
//
// Replies are checked character by character: a stray control character shows
// as a box in the console and fails the check.

export default async function squadConsole(helpers) {
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
  const noPlayers = ["----- Active Players -----", "----- Recently Disconnected Players [Max of 15] -----"];

  await waitFor("window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_config === 'function'", 10000);

  await click("#addBtn");
  await settle("modal");
  await evaluate("(() => { const s = document.getElementById('fGame'); s.value = 'squad'; s.dispatchEvent(new Event('change', {bubbles: true})); })()");
  await type("#fHost", fixture.host);
  await type("#fPort", String(fixture.port));
  await click("#saveBtn");
  await waitFor("document.querySelectorAll('[data-card]').length === 1 && !document.getElementById('modal').classList.contains('show')", 5000);

  // The server opens RCON a few seconds after the fixture starts it, so Connect is
  // retried until it answers. Lines from failed attempts are not judged.
  let connected = false;
  for (let attempt = 0; attempt < 25 && !connected; attempt++) {
    await evaluate("document.querySelector('[data-card] [data-pw]').value = ''");
    await type("[data-card] [data-pw]", fixture.password);
    await click("[data-card] [data-connect]");
    connected = await waitFor("document.querySelector('[data-card]').classList.contains('connected')", 12000)
      .then(() => true).catch(() => false);
    if (!connected) await sleep(2000);
  }
  check("connected to the Squad server", connected, JSON.stringify(await lines()));

  // Until the layer has loaded, commands get no reply within the app's 8 s timeout.
  // List Players is pressed until it answers; those presses are not judged.
  let warm = false;
  for (let attempt = 0; attempt < 10 && !warm; attempt++) {
    warm = same(await press("List Players"), noPlayers);
  }
  check("the server answered once the layer loaded", warm, JSON.stringify(await lines()));
  const start = (await lines()).length;

  const players1 = await press("List Players");
  check("List Players: reply is the empty player list", same(players1, noPlayers), JSON.stringify(players1) + ` after ${tookMs["List Players"]} ms`);

  const squads = await press("List Squads");
  check("List Squads: reply lists both teams and their tickets",
        squads.length === 3 && squads[0] === "----- Active Squads -----" &&
        /^Team ID: 1 \(.+\) - Tickets: \d+$/.test(squads[1]) && /^Team ID: 2 \(.+\) - Tickets: \d+$/.test(squads[2]),
        JSON.stringify(squads));

  // The fixture's rotation holds one layer, so the next layer is the current one.
  const next = await press("Show Next Map");
  check("Show Next Map: reply names the next layer",
        same(next, ["Next level is Al Basrah, layer is AlBasrah_AAS_v1, factions USMC MEI"]), JSON.stringify(next));

  const broadcast = await press("Broadcast", "SRT app check");
  check("Broadcast: reply is the server's broadcast line", same(broadcast, ["Message broadcasted <SRT app check>"]), JSON.stringify(broadcast));

  // The server needs an ID and a reason; no player has ID 99.
  const kick = await press("Kick by ID", "99 SRT app check");
  check("Kick by ID: reply is the server's not-found line", same(kick, ["ERROR: Unable to find player with id (99)"]), JSON.stringify(kick));

  const end = await press("End Match", undefined, true);
  check("End Match: reply is the server's ended line", same(end, ["Match ended"]), JSON.stringify(end));

  const restart = await press("Restart Match", undefined, true);
  check("Restart Match: reply is the server's restarted line", same(restart, ["Game restarted"]), JSON.stringify(restart));

  // The restart reloads the layer (about 10 s); the connection stays open through it.
  await sleep(20000);
  const players2 = await press("List Players");
  check("List Players: the connection still answers after the restart", same(players2, noPlayers), JSON.stringify(players2) + ` after ${tookMs["List Players"]} ms`);

  const all = (await lines()).slice(start);
  const marked = all.filter(l => /<\d+>/.test(l));
  check("no control characters anywhere in the console", marked.length === 0, JSON.stringify(marked));
  const errors = await evaluate(
    `Array.from(document.querySelectorAll('#console .ln')).slice(${start}).filter(l => l.classList.contains('err')).map(l => l.textContent)`);
  check("no error lines in the console once connected", errors.length === 0, JSON.stringify(errors));
  await screenshot("console");
}
