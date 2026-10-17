// ui_drive scenario for Simple RCON Tool (see build-tools/ui_drive/README.md
// for the helpers and how this is launched). Starts from a fresh app (no
// servers.json) and drives the real window through adding a server with two
// refused saves, an edit, a refused connect, console export and clear, a
// delete, and a theme round trip confirmed through the real Python bridge.
//
// Stays offline: it never connects to a game server. The only connect
// attempt has no password, which the app refuses before opening any
// connection. The test server's host is 192.0.2.10, an address reserved for
// documentation that no real machine uses.
//
// Does not cover: real RCON connections, quick buttons, custom buttons,
// the debug log toggle, or the update check. Those stay for a person to
// check by hand.

export default async function smoke(helpers) {
  const { click, type, press, evaluate, waitFor, check, screenshot } = helpers;

  // Wait until a modal is shown and its rise animation has finished, so a
  // click or type() does not land while it is still moving.
  const settle = (id) => waitFor(
    `document.getElementById('${id}').classList.contains('show') && ` +
    `document.querySelector('#${id} .modal').getAnimations().length === 0`, 5000);
  const shown = (id) => evaluate(`document.getElementById('${id}').classList.contains('show')`);
  const text = (sel) => evaluate(`(document.querySelector(${JSON.stringify(sel)}) || {}).textContent || ''`);
  const errLines = () => evaluate(
    "Array.from(document.querySelectorAll('#console .ln.err .msg')).map(m => m.textContent)");

  // a) the page-to-Python link answers, with the real version, on a fresh app.
  await waitFor("window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_config === 'function'", 10000);
  await waitFor("document.getElementById('verLabel').textContent.length > 0", 10000);
  const cfg1 = await evaluate("api().get_config()");
  check("get_config answers over the real bridge", !!cfg1 && typeof cfg1.version === "string", JSON.stringify(cfg1 && cfg1.version));
  const verLabel = await text("#verLabel");
  check("version label matches the version get_config returned",
    verLabel === "v" + cfg1.version && /^v\d+\.\d+\.\d+$/.test(verLabel), `${verLabel} vs v${cfg1.version}`);
  check("starts with no servers (fresh app)", Array.isArray(cfg1.servers) && cfg1.servers.length === 0,
    JSON.stringify(cfg1.servers));
  const emptyList = await text("#serverList");
  check("server list shows the empty message", /No servers yet/.test(emptyList), emptyList);

  // b) add a server, with a refused save before a game is picked.
  await click("#addBtn");
  await settle("modal");
  await click("#saveBtn");
  const noGameErr = await text("#modalErr");
  check("saving with no game is refused", noGameErr === "Pick a game from the list.", noGameErr);

  // The game list is a native dropdown. Arrow Down on the focused list picks
  // the first game without opening the popup, the same as a keyboard user.
  // Focus is set directly because clicking the list would open that popup.
  await evaluate("document.getElementById('fGame').focus()");
  await press("ArrowDown");
  const game = await evaluate("document.getElementById('fGame').value");
  check("first game picked with the keyboard", game === "ark_se", game);
  const prefill = await evaluate("document.getElementById('fName').value");
  check("display name prefilled with the game name", prefill === "ARK: Survival Evolved", prefill);
  const chips = await evaluate("document.querySelectorAll('#lockedPreview .locked-chip').length");
  check("the game's built-in buttons are previewed", chips > 0, String(chips));

  await type("#fName", "Test Server");
  await type("#fHost", "192.0.2.10");
  await type("#fPort", "not a port");
  await click("#saveBtn");
  const portErrShown = await waitFor("document.getElementById('modalErr').textContent === 'Port must be a number from 1 to 65535.'", 5000)
    .then(() => true).catch(() => false);
  check("a non-number port is refused by Python", portErrShown, await text("#modalErr"));
  const cfgAfterBadPort = await evaluate("api().get_config()");
  check("nothing was saved for the bad port", cfgAfterBadPort.servers.length === 0, JSON.stringify(cfgAfterBadPort.servers));

  await type("#fPort", "27020");
  await click("#saveBtn");
  const cardShown = await waitFor("document.querySelectorAll('[data-card]').length === 1 && !document.getElementById('modal').classList.contains('show')", 5000)
    .then(() => true).catch(() => false);
  check("server card appears and the editor closes", cardShown);
  const meta = await text("[data-card] .card-meta");
  check("card shows the game, host and port", meta.includes("ARK: Survival Evolved") && meta.includes("192.0.2.10:27020"), meta);
  const cfgAfterAdd = await evaluate("api().get_config()");
  const saved = cfgAfterAdd.servers[0] || {};
  check("server saved through Python with a numeric port",
    cfgAfterAdd.servers.length === 1 && saved.name === "Test Server" && saved.host === "192.0.2.10" && saved.port === 27020,
    JSON.stringify(cfgAfterAdd.servers));
  check("no password stored with the server", !("password" in saved), JSON.stringify(Object.keys(saved)));
  await screenshot("server-added");

  // c) error path: connecting with no password is refused before any network.
  await click("[data-card] [data-connect]");
  const pwErrShown = await waitFor(
    "Array.from(document.querySelectorAll('#console .ln.err .msg')).some(m => m.textContent === 'RCON password is required.')", 5000)
    .then(() => true).catch(() => false);
  check("connect with no password is refused with a plain-language message", pwErrShown, JSON.stringify(await errLines()));
  const connected = await evaluate("document.querySelector('[data-card]').classList.contains('connected')");
  check("card is not marked connected", connected === false, String(connected));
  const connectLabel = await text("[data-card] [data-connect]");
  check("connect button is back to its normal label", connectLabel === "Connect", connectLabel);

  // d) edit the server's name.
  const idBefore = saved.id;
  await click("[data-card] [data-edit]");
  await settle("modal");
  const editTitle = await text("#modalTitle");
  check("editor opens in edit mode", editTitle === "Edit Server", editTitle);
  const hostPrefill = await evaluate("document.getElementById('fHost').value");
  check("editor is prefilled with the saved host", hostPrefill === "192.0.2.10", hostPrefill);
  await type("#fName", "Renamed Server");
  await click("#saveBtn");
  await waitFor("!document.getElementById('modal').classList.contains('show')", 5000);
  const cardName = await text("[data-card] .card-name");
  check("card shows the new name", cardName.includes("Renamed Server"), cardName);
  const cfgAfterEdit = await evaluate("api().get_config()");
  const edited = cfgAfterEdit.servers[0] || {};
  check("edit saved over the same server, not a new one",
    cfgAfterEdit.servers.length === 1 && edited.id === idBefore && edited.name === "Renamed Server",
    JSON.stringify(cfgAfterEdit.servers));

  // e) console export and clear. The refused connect left a line to export.
  await click("#exportBtn");
  const exported = await waitFor(
    "Array.from(document.querySelectorAll('#console .ln.ok .msg')).some(m => m.textContent.startsWith('Console exported: '))", 5000)
    .then(() => true).catch(() => false);
  const exportLine = await evaluate(
    "(Array.from(document.querySelectorAll('#console .ln .msg')).map(m => m.textContent).find(t => /^(Console exported|Export failed)/.test(t))) || ''");
  check("console export reports the saved file",
    exported && /^Console exported: RCON_Console_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.txt$/.test(exportLine), exportLine);
  await click("#clearBtn");
  const cleared = await evaluate(
    "document.querySelectorAll('#console .ln').length === 0 && document.getElementById('console').textContent.includes('Console cleared.')");
  check("console clears", cleared);

  // f) delete the server through the confirm dialog.
  await click("[data-card] [data-del]");
  await settle("confirm");
  const confirmTitle = await text("#confirmTitle");
  check("delete asks for confirmation", confirmTitle === "Delete server?", confirmTitle);
  await click("#confirmYes");
  const deleted = await waitFor("document.querySelectorAll('[data-card]').length === 0", 5000)
    .then(() => true).catch(() => false);
  check("server card is removed", deleted);
  const cfgAfterDelete = await evaluate("api().get_config()");
  check("delete saved through Python", cfgAfterDelete.servers.length === 0, JSON.stringify(cfgAfterDelete.servers));

  // g) theme round trip, screenshot of each, confirmed saved through Python.
  const startLight = await evaluate("document.body.classList.contains('light')");
  await click("#theme-btn");
  await waitFor(`document.body.classList.contains('light') === ${!startLight}`, 5000);
  const afterFirstToggle = await evaluate("document.body.classList.contains('light')");
  check("theme toggled to the other theme", afterFirstToggle === !startLight, String(afterFirstToggle));
  await screenshot(afterFirstToggle ? "theme-light" : "theme-dark");
  const themeAfterFirst = await evaluate("api().get_theme()");
  check("theme change after the first toggle was saved through Python",
    themeAfterFirst === (afterFirstToggle ? "light" : "dark"), JSON.stringify(themeAfterFirst));

  await click("#theme-btn");
  await waitFor(`document.body.classList.contains('light') === ${startLight}`, 5000);
  const afterSecondToggle = await evaluate("document.body.classList.contains('light')");
  check("theme toggled back to the starting theme", afterSecondToggle === startLight, String(afterSecondToggle));
  await screenshot(afterSecondToggle ? "theme-light" : "theme-dark");
  const themeAfterSecond = await evaluate("api().get_theme()");
  check("theme change after the second toggle was saved through Python",
    themeAfterSecond === (afterSecondToggle ? "light" : "dark"), JSON.stringify(themeAfterSecond));

  // h) nothing left open or complaining at the end.
  const anyOpen = [];
  for (const id of ["modal", "confirm", "prompt", "updateErrModal"]) {
    if (await shown(id)) anyOpen.push(id);
  }
  check("no dialog left open", anyOpen.length === 0, anyOpen.join(", "));
  const leftoverErrs = await errLines();
  check("no error lines left in the console", leftoverErrs.length === 0, JSON.stringify(leftoverErrs));
}
