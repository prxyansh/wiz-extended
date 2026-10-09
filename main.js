const { app, BrowserWindow, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');
const kill = require('tree-kill');

let mainWindow;
let pythonProcess;
const PORT = 8899;

function createPythonProcess() {
  const isWin = process.platform === 'win32';
  
  // Use the local venv python if available, otherwise fallback to system python
  const pythonPath = isWin ? 
    path.join(__dirname, '.venv', 'Scripts', 'python.exe') : 
    path.join(__dirname, '.venv', 'bin', 'python3');

  console.log(`Starting Python server on port ${PORT}...`);
  pythonProcess = spawn(pythonPath, ['server.py'], {
    cwd: __dirname,
    stdio: 'inherit'
  });

  pythonProcess.on('error', (err) => {
    console.error('Failed to start python subprocess:', err);
    dialog.showErrorBox(
      'Python Error',
      'Failed to start the backend server. Is Python installed and the virtual environment active?'
    );
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1000,
    height: 800,
    title: "Philips WiZ Extended",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true
    }
  });

  // Hide menu bar for a cleaner app feel
  mainWindow.setMenuBarVisibility(false);

  // Poll for the Python server to be ready before loading
  let attempts = 0;
  const checkServer = setInterval(() => {
    http.get(`http://127.0.0.1:${PORT}`, (res) => {
      if (res.statusCode === 200) {
        clearInterval(checkServer);
        mainWindow.loadURL(`http://127.0.0.1:${PORT}`);
      }
    }).on('error', (err) => {
      attempts++;
      if (attempts > 30) {
        clearInterval(checkServer);
        dialog.showErrorBox('Timeout', 'Backend server took too long to start.');
      }
    });
  }, 200);

  mainWindow.on('closed', function () {
    mainWindow = null;
  });
}

app.on('ready', () => {
  createPythonProcess();
  createWindow();
});

app.on('window-all-closed', function () {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', function () {
  if (mainWindow === null) createWindow();
});

// Cleanly kill the python subprocess tree when electron closes
app.on('quit', () => {
  if (pythonProcess && pythonProcess.pid) {
    kill(pythonProcess.pid, 'SIGKILL');
  }
});
