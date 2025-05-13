# QuantConnect Local Lean Setup
This is instruction to setup Quantconnect to run locally on windows without using docker <br />
Assume you have some knowledge using vscode 

## Install latest dotnet framework
https://dotnet.microsoft.com/en-us/download/dotnet/9.0

## debugpy bugs fixed (non docker)
First download QuantConnect Lean using tool such as Github Desktop
```
git clone https://github.com/QuantConnect/Lean
```

In AlgorithmFactory\DebuggerHelper.cs:
```
import debugpy <---after this line insert the following:
import os,sys
python_executable_in_venv = os.path.join(sys.prefix, 'Scripts', 'python.exe')
debugpy.configure(python=python_executable_in_venv)
```
## Setup python virtual environment
In the downloaded quantconnect root folder (assuming you have python 3.11 or newer installed in your system already):
```
python -m venv .venv
.venv\Scripts\activate
python.exe -m pip install --upgrade pip
pip install  jupyterlab pandas==2.1.4 wrapt==1.16.0 clr_loader==0.1.6 matplotlib debugpy
python -m ipykernel install --user --name="myvenv" --display-name="python lean"
pip install --no-cache-dir quantconnect-stubs

```
Make sure you select .venv as the python interpreter

## Setup config.json
Setup python environment in Launcher/config.json

## Compile Quantconnect
Debug mode:
```
dotnet build QuantConnect.Lean.sln
```
Release mode:
```
dotnet build QuantConnect.Lean.sln -c Release
```

## Launching
You can start running and debugging QC algorithm by specifying the python program in config.json. <br />
Goto Run and Debug (ctrl shift D) and click launch<br />
Wait for the console to show listening to port 5678 <br />
set break point (if you want to debug) and click Attach to python <br />
There will be no graphical output, it will show the sharpe and statistics

## Quatbook Research
open a git bash terminal and execute the quantbook jupyter notebook:
```
cd .vscode
sh .vscode/launch_research.sh 
```
Look for BasicQuantBookTemplate.ipynb to see how to start quantbook
There is a minor ammendment: The first code to execute is:
```
%run start.py
```
