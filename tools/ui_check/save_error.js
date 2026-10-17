// ui_drive scenario for Simple RCON Tool (see build-tools/ui_drive/README.md
// for the helpers and how this is launched). Starts with a saved server whose
// file is held open by the fixture, so edits and deletes fail without changing
// the saved list.
//
// Stays offline: it uses 192.0.2.10, an address reserved for documentation
// that no real machine uses, and never attempts a connection.

export default async function saveError(helpers) {
  const { click, type, evaluate, waitFor, check } = helpers;
  const saveErrorText = 'The server could not be saved. Your saved list was not changed.';
  const deleteErrorText = 'The server could not be deleted. Your saved list was not changed.';
  const settle = (id) => waitFor(
    `document.getElementById('${id}').classList.contains('show') && ` +
    `document.querySelector('#${id} .modal').getAnimations().length === 0`, 5000);
  const text = (sel) => evaluate(`(document.querySelector(${JSON.stringify(sel)}) || {}).textContent || ''`);
  const errLines = () => evaluate(
    "Array.from(document.querySelectorAll('#console .ln.err .msg')).map(m => m.textContent)");

  await waitFor("window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_config === 'function'", 10000);
  await waitFor("document.querySelectorAll('[data-card]').length === 1", 10000);
  check("saved server card appears", await text("[data-card] .card-name") === "Original Server",
    await text("[data-card] .card-name"));

  await click("[data-card] [data-edit]");
  await settle("modal");
  await type("#fName", "Renamed Server");
  await click("#saveBtn");
  const saveErrorShown = await waitFor(
    `document.getElementById('modalErr').textContent === ${JSON.stringify(saveErrorText)}`, 5000)
    .then(() => true).catch(() => false);
  check("failed save keeps its error in the editor", saveErrorShown, await text("#modalErr"));
  await click("#cancelBtn");
  check("failed save leaves the card unchanged", await text("[data-card] .card-name") === "Original Server",
    await text("[data-card] .card-name"));

  await click("[data-card] [data-del]");
  await settle("confirm");
  await click("#confirmYes");
  const deleteErrorShown = await waitFor(
    `Array.from(document.querySelectorAll('#console .ln.err .msg')).some(m => m.textContent === ${JSON.stringify(deleteErrorText)})`,
    5000).then(() => true).catch(() => false);
  check("failed delete appears in the console", deleteErrorShown, JSON.stringify(await errLines()));
  check("failed delete leaves the card", await evaluate("document.querySelectorAll('[data-card]').length === 1"),
    await text("[data-card] .card-name"));

  const config = await evaluate("api().get_config()");
  check("saved list remains the original server", config.servers.length === 1 &&
    config.servers[0].id === "srv-one" && config.servers[0].name === "Original Server", JSON.stringify(config.servers));
}
