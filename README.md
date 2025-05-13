# QCSetup
This is instruction to setup Quantconnect to run locally on windows without using docker <br />
Assume you have some knowledge using vscode 

## Install latest dotnet framework

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
pip install  jupyterlab pandas==2.1.4 wrapt==1.16.0 clr_loader==0.1.6 matplotlib jupyter
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
Goto Run and Debug (ctrl shift D) and click launch
set break point and click Attach to python

## Quatbook Research
open a git bash and execute the quantbook jupyter notebook:
```
cd .vscode
sh .vscode/launch_research.sh 
```
Look for BasicQuantBookTemplate.ipynb to see how to use start quantbook
There is a minor ammendment need to be made. The first code to execute is:
```
%run start.py
```
