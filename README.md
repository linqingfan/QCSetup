# QuantConnect Local Lean Setup

This guide provides instructions to set up QuantConnect Lean to run locally on Windows without using Docker.
It assumes you have some familiarity with VS Code.

## Install the Latest .NET
Download and install the latest version of .NET (e.g., .NET 9.0 or newer) from the official website:
https://dotnet.microsoft.com/en-us/download/dotnet/9.0

## `debugpy` bugs fixed (non-docker)
First, download (clone) the QuantConnect Lean repository using a tool such as GitHub Desktop or the command line:
```bash
git clone https://github.com/QuantConnect/Lean
```

In `AlgorithmFactory/DebuggerHelper.cs`:
```
import debugpy <---after this line insert the following:
import os,sys
python_executable_in_venv = os.path.join(sys.prefix, 'Scripts', 'python.exe')
debugpy.configure(python=python_executable_in_venv)
```

## Set up a Python Virtual Environment
Need to set the environment variable **PYTHONNET_PYDLL** to the system python dll <br />
Something like this C:\Users\username\AppData\Local\Programs\Python\Python311\python311.dll <br />
In the downloaded QuantConnect root folder (assuming you already have Python 3.11 installed on your system):
```bash
python -m venv .venv
.venv\Scripts\activate
python.exe -m pip install --upgrade pip
pip install jupyterlab pandas==2.1.4 wrapt==1.16.0 clr_loader==0.1.6 matplotlib debugpy
python -m ipykernel install --user --name="myvenv" --display-name="Python Lean"
pip install --no-cache-dir quantconnect-stubs
```
After this, in VS Code, make sure you select the `.venv` environment as your Python interpreter.

## Configure `config.json`
Set up the Python environment path in `Launcher/config.json` according to the comment in the json file.

## Compile QuantConnect
To compile Lean:

**Debug mode:**
```bash
dotnet build QuantConnect.Lean.sln
```

**Release mode:**
```bash
dotnet build QuantConnect.Lean.sln -c Release
```
If you compile in Release mode, make sure to replace all other settings that have `Debug` with `Release` e.g. in `.vscode/launch.json`

## Launching and Debugging
You can start running and debugging a QuantConnect algorithm by specifying the Python algorithm script path in `Launcher/config.json` (e.g., under the `algorithm-location` settings).

1.  In VS Code, go to "Run and Debug" (Ctrl+Shift+D).
2.  Select the "launch" configuration.
3.  Click the "play" icon
4.  Wait for the console to show a message like "Debugger listening on port 5678".
5.  Set breakpoints in your Python algorithm code (if desired).
6.  In the "Run and Debug" view, select the "Attach to Python" configuration and click play.

There will be no graphical chart output during local debugging; the console will display the Sharpe ratio and other statistics after the backtest completes.

## Disabling Pre-Launch Build
If there are no code changes in the QuantConnect C# system files, you can turn off the automatic rebuild that occurs each time you launch a debug session. This can speed up iteration.

To do this, comment out the `preLaunchTask` line in `.vscode/launch.json`:
```
// "preLaunchTask": "build",
```
If you do make changes to the C# system files, you will need to recompile them manually as described in the [Compile QuantConnect](#compile-quantconnect) section before launching.

## QuantBook Research
To use QuantBook for research:

1.  Open a Git Bash terminal (or any terminal that can execute shell scripts).
2.  Navigate to your QuantConnect Lean project root directory.
3.  Execute the QuantBook Jupyter Notebook launch script in .vscode directory:
    ```bash
    cd .vscode
    sh launch_research.sh 
    ```
In jupyter environment, look for `BasicQuantBookTemplate.ipynb` to see an example of how to start using QuantBook.

There is a minor amendment: The first code cell to execute in the notebook should be:
```python
%run start.py
```
