# YouTube Downloader - Cross-Platform App

A desktop and web application for downloading YouTube playlists with intelligent folder picker support.

## Features

- 🎵 Download YouTube playlists
- 📁 **Auto folder selection**: Uses app's downloads folder (`./downloads/`)
- 🎨 Modern web UI
- ⚡ Fast downloads with yt-dlp
- 📱 Cross-platform (Windows, macOS, Linux, Web, Mobile)
- 💾 Consistent download location

## Installation

1. **Install Node.js** (if not already installed)
   ```bash
   # On Ubuntu/Debian
   curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
   sudo apt-get install -y nodejs

   # On macOS
   brew install node

   # On Windows: Download from https://nodejs.org/
   ```

2. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Electron dependencies**
   ```bash
   npm install
   ```

## Running the App

### Development Mode
```bash
npm run dev
```

### Production Build
```bash
npm run build
```

### Run Built App
```bash
npm start
```

## How It Works

- **Electron Main Process**: Manages the application window and native system dialogs
- **Flask Backend**: Handles YouTube downloads and serves the web UI
- **Web UI**: Modern interface with native folder picker integration

## Folder Picker Implementation

The app intelligently adapts the folder picker based on your environment:

### 🖥️ **Desktop App (Electron)**
```javascript
const { dialog } = require("electron");
const result = await dialog.showOpenDialog({
  properties: ["openDirectory"]
});
const folderPath = result.filePaths[0];
```
- ✅ Full file system access
- ✅ Native OS dialogs
- ✅ Exact local paths
- ✅ No browser sandbox

### 🌐 **Web Browsers (Chrome/Edge)**
```html
<input type="file" webkitdirectory>
```
- ✅ HTML5 directory picker
- ✅ Works in modern browsers
- ⚠️ Limited to browser-accessible folders

### 📱 **Mobile Browsers**
```
Manual text input with local storage
```
- ✅ Works on all mobile devices
- ✅ Remembers your choices
- ✅ Custom path entry
- ⚠️ Manual path input required

## Troubleshooting Folder Picker

### Getting "Folder picker is only available in the desktop app"?

This message should no longer appear with the cross-platform implementation. If you still see it:

1. **Clear browser cache** - Hard refresh (Ctrl+F5) or clear cache
2. **Check browser console** (F12) for debug information
3. **Button indicators**:
   - 🖥️ **"Choose Folder (Native)"** = Electron desktop app
   - 🌐 **"Choose Folder (Browser)"** = Web browser with HTML5 support
   - 📱 **"Choose Folder (Manual)"** = Mobile or limited browser

### Environment Detection

The app automatically detects your environment:
- **Electron**: `window.electronAPI` exists
- **Mobile**: User agent contains mobile keywords
- **Webkit Support**: `'webkitdirectory' in document.createElement('input')`

### Testing

Run the test script to verify functionality:
```bash
./test-folder-picker.sh
```

This will start the Flask app and provide testing instructions.