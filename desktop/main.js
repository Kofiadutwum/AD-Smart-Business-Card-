const { app, BrowserWindow, shell } = require("electron");
const path = require("path");

const SITE = "https://adsmarttech.com";
const START_URL = `${SITE}/staff/`;

if (!app.requestSingleInstanceLock()) {
  app.quit();
}

let win = null;

function isSite(url) {
  try {
    return new URL(url).origin === SITE;
  } catch {
    return false;
  }
}

function openInBrowser(url) {
  if (/^(https?|mailto|tel):/i.test(url)) shell.openExternal(url);
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 380,
    minHeight: 600,
    title: "AD Smart",
    icon: path.join(__dirname, "build", "icon.ico"),
    autoHideMenuBar: true,
    backgroundColor: "#1f1f1f",
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true },
  });

  win.loadURL(START_URL);

  // Pages of the site stay in this window; any other address opens in the default browser.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isSite(url)) win.loadURL(url);
    else openInBrowser(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (!isSite(url) && !url.startsWith("file:")) {
      event.preventDefault();
      openInBrowser(url);
    }
  });

  // No connection: show the local retry page. Code -3 is a cancelled load, not a failure.
  win.webContents.on("did-fail-load", (_event, code, _description, _url, isMainFrame) => {
    if (isMainFrame && code !== -3) win.loadFile(path.join(__dirname, "offline.html"));
  });

  win.on("closed", () => {
    win = null;
  });
}

app.on("second-instance", () => {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.focus();
});

app.whenReady().then(createWindow);
app.on("window-all-closed", () => app.quit());
