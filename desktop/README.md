# AD Smart desktop app (Windows and Mac)

A window around the live site, https://adsmarttech.com, for staff. It opens the staff area; links to other sites open in the default browser. With no internet it shows a retry page. Everything else is the website itself, so site updates reach the app without a new installer.

## Build the installer

```
cd desktop
npm install
npm run dist
```

The installer is written to `dist/AD-Smart-Setup-<version>.exe`. To try the app without building: `npm start`.

## Build the Mac app

A Mac app can only be built on a Mac, so GitHub builds it: on the repository's Actions tab, run "Build Mac app", then download the `AD-Smart-mac` file from the finished run. It contains `AD-Smart-<version>-mac.dmg`, one app for both Intel and Apple Silicon Macs. On a Mac you can also build it directly with `npm run dist:mac`.

The app is not signed with an Apple developer certificate, so the first time: open the `.dmg`, drag AD Smart to Applications, then in Applications right-click AD Smart, choose Open, and confirm. If macOS still refuses, go to System Settings, Privacy & Security, and choose Open Anyway.

## Notes

- The installer is not code-signed, so Windows SmartScreen shows "unknown publisher" the first time: choose More info, then Run anyway.
- If the build stops with `EBUSY` (Windows locking files inside the synced folder), build outside Dropbox: `npm run dist -- --config.directories.output=C:/ad-smart-build`.
- Raise `version` in `package.json` before building a new installer.
- `node_modules/` and `dist/` are not committed and are excluded from Dropbox sync.
