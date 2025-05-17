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
pip install packages wheel jupyterlab pandas==2.1.4 wrapt==1.16.0 clr_loader==0.1.6 matplotlib debugpy
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
# Guide: Downloading IBKR Data & Converting to LEAN Format

This guide outlines the steps to download 1-minute historical data from Interactive Brokers (IBKR) using a Python script and then convert that data into the format required by the QuantConnect LEAN trading engine for local backtesting.

## Prerequisites

- **Python 3.x** installed.
- **Required Python Libraries:**
  - `ibapi` (Interactive Brokers Python API client)
  - `pandas`
  - `pytz`

  You can install them using pip:

  ```bash
  pip install ibapi pandas pytz
  ```

- **Interactive Brokers Account:** You need an IBKR account with market data subscriptions for the instruments you wish to download.
- **IBKR Trader Workstation (TWS) or IB Gateway:** One of these must be running and configured for API connections.
  - In TWS/Gateway: Go to **File > Global Configuration > API > Settings**.
  - Enable "Enable ActiveX and Socket Clients".
  - Note the "Socket port" (e.g., 7497 for TWS, 4002 for Gateway).
  - Optionally, add `127.0.0.1` to "Trusted IP Addresses" if your script runs on the same machine.
- **QuantConnect LEAN Engine (for local backtesting):** If your goal is to use this data with LEAN locally, you should have LEAN set up.

---

## Part 1: Downloading 1-Minute Data from IBKR

This part uses a Python script (let's call it `download_ibkr_data.py` - this refers to the script you provided earlier, which was refined to read from `config.json`).

### Step 1.1: Prepare Configuration Files

You will need two JSON configuration files in the same directory as `download_ibkr_data.py`:

#### A. `config.json` (for the download script)

This file controls the download parameters, contract details, API settings, and date ranges.

```json
{
  "contract": {
    "symbol": "AAPL",
    "secType": "STK",
    "exchange": "SMART",
    "currency": "USD",
    "primaryExchange": "NASDAQ"
  },
  "date_range": {
    "start_year": 2022,
    "start_month": 1,
    "start_day": 1,
    "end_year": 2023,
    "end_month": 12,
    "end_day": 31
  },
  "api_settings": {
    "ibkr_port": 7497,
    "client_id": 10,
    "max_active_requests": 5,
    "inter_request_delay_seconds": 2,
    "max_connect_retries": 5,
    "connect_retry_delay_seconds": 30,
    "max_request_retries": 3,
    "request_retry_delay_seconds": 10
  },
  "output": {
    "filename_template": "{symbol}_1min_{start_year}_{end_year}_incremental.csv"
  },
  "timezone_overrides": {
    "exchange_map": {
        "SEHK": "Asia/Hong_Kong",
        "NSE": "Asia/Kolkata",
        "IDEALPRO": "UTC",
        "NASDAQ": "America/New_York",
        "NYSE": "America/New_York",
        "ARCA": "America/New_York"
    },
    "currency_map": {
        "HKD": "Asia/Hong_Kong",
        "INR": "Asia/Kolkata",
        "USD": "America/New_York",
        "EUR": "Europe/Berlin",
        "JPY": "Asia/Tokyo"
    },
    "default_fallback": "America/New_York"
  }
}
```

**Key fields to customize in `config.json`:**

- `contract`: Define the symbol, security type, exchange, currency, and optionally primary exchange.
- `date_range`: Specify the overall start and end dates for the data download.
- `api_settings.ibkr_port`: Match this to your TWS/Gateway socket port.
- `api_settings.client_id`: A unique integer for this API connection.
- `timezone_overrides`: Adjust if necessary, though the defaults cover common cases.

### Step 1.2: Run TWS or IB Gateway

Ensure your TWS or IB Gateway application is running and you are logged in. The API settings must be configured as mentioned in the prerequisites.

> **Important:** Interactive Brokers typically allows a user account to have an active trading session (including API) from only **one IP address at a time**. If you get errors like "Trading TWS session is connected from a different IP address," ensure you are not logged into TWS/Gateway elsewhere with the same user credentials.

### Step 1.3: Execute the Download Script

Open your terminal or command prompt, navigate to the directory containing `download_ibkr_data.py` and `config.json`, and run the script:

```bash
python download_ibkr_data.py
```

The script will:

- Read settings from `config.json`.
- Connect to TWS/Gateway.
- Request 1-minute historical data in weekly chunks.
- Handle timezones, converting data to UTC.
- Save the data incrementally to a CSV file (e.g., `AAPL_1min_2022_2023_incremental.csv`).
- The script includes resume capabilities; if run again, it will attempt to pick up where it left off based on the last date in the CSV.

*Monitor the console output for progress and any error messages.*

---

## Part 2: Converting IBKR Data to LEAN Format

This part uses a Python script (let's call it `convert_ibkr_to_lean.py` - this refers to the second script you provided).

### Step 2.1: Prepare Configuration File for Conversion

#### A. `market_config.json` (for the conversion script)

This file defines market-specific parameters like standard trading hours, pre/post market sessions, and weekend days. Create this file in the same directory as `convert_ibkr_to_lean.py`.

```json
{
  "hkfe": {
    "timezone": "Asia/Hong_Kong",
    "preMarketStart": "09:00:00",
    "preMarketEnd": "09:30:00",
    "marketStart": "09:30:00",
    "marketEnd": "16:00:00",
    "postMarketStart": "16:00:00",
    "postMarketEnd": "16:10:00",
    "weekendDays": [5, 6]
  },
  "usa": {
    "timezone": "America/New_York",
    "preMarketStart": "04:00:00",
    "preMarketEnd": "09:30:00",
    "marketStart": "09:30:00",
    "marketEnd": "16:00:00",
    "postMarketStart": "16:00:00",
    "postMarketEnd": "20:00:00",
    "weekendDays": [5, 6]
  },
  "sgx": {
    "timezone": "Asia/Singapore",
    "preMarketStart": "08:30:00",
    "preMarketEnd": "09:00:00",
    "marketStart": "09:00:00",
    "marketEnd": "17:00:00",
    "postMarketStart": "17:00:00",
    "postMarketEnd": "17:06:00",
    "weekendDays": [5, 6]
  }
}
```

**Key fields to customize in `market_config.json`:**

- Add entries for each market you need (e.g., `"usa"`, `"hkfe"`). The key should match the `-m` or `--market` argument you'll use when running the script.
- `timezone`: The official IANA timezone string for the market (e.g., `"America/New_York"`, `"Asia/Hong_Kong"`).
- `preMarketStart`, `preMarketEnd`, `marketStart`, `marketEnd`, `postMarketStart`, `postMarketEnd`: Define the session times in HH:MM:SS format in the market's local time.
- `weekendDays`: A list of integers representing weekend days (0=Monday, ..., 6=Sunday). Typically `[5, 6]`.

### Step 2.2: Execute the Conversion Script

Open your terminal or command prompt, navigate to the directory containing `convert_ibkr_to_lean.py` and `market_config.json`. Run the script with the appropriate arguments:

```bash
python convert_ibkr_to_lean.py [INPUT_CSV_PATH] [OUTPUT_LEAN_DATA_DIR] [TICKER] -m [MARKET_CODE]
```

**Argument Explanation:**

- `[INPUT_CSV_PATH]`: Path to the CSV file generated by the download script (e.g., `AAPL_1min_2022_2023_incremental.csv`).
- `[OUTPUT_LEAN_DATA_DIR]`: Path to your root LEAN data folder (e.g., `./data` or the `Data` directory in your LEAN project).
- `[TICKER]`: The ticker symbol (e.g., `AAPL`). This should be in lowercase for LEAN file naming conventions. The script will handle lowercasing.
- `-m [MARKET_CODE]` or `--market [MARKET_CODE]`: The LEAN market code (e.g., `usa`, `hkfe`). This must match a key in your `market_config.json`. Defaults to `hkfe` if not provided.

**Example Command:**

```bash
python convert_ibkr_to_lean.py AAPL_1min_2022_2023_incremental.csv ./lean_data_output AAPL -m usa
```

The conversion script will:

1. Read the IBKR data CSV.
2. Load market parameters from `market_config.json`.
3. Convert UTC timestamps to the local market time.
4. **Adjust timestamps:** IBKR bar timestamps (start of bar) are shifted by +1 minute to represent the LEAN convention (end of bar).
5. Calculate LEAN's millisecond-based time format.
6. Scale prices by 10000.
7. Create daily zipped CSV files in the LEAN directory structure:

   ```
   [OUTPUT_LEAN_DATA_DIR]/equity/[MARKET_CODE]/minute/[ticker_lowercase]/[YYYYMMDD]_trade.zip
   ```

   (e.g., `./lean_data_output/equity/usa/minute/aapl/20230103_trade.zip`)

8. Generate a map file for the ticker in:

   ```
   [OUTPUT_LEAN_DATA_DIR]/equity/[MARKET_CODE]/map_files/
   ```

9. Generate a `[market]_market_hours_entry.json` file (e.g., `usa_market_hours_entry.json`) in the same directory as the script.

### Step 2.3: Update LEAN's Market Hours Database (Manual Step)

> **This is a crucial manual step for correct backtesting in LEAN.**

The conversion script generates a file like `usa_market_hours_entry.json`. You need to integrate its contents into LEAN's main market hours database.

1. Locate the generated `[market]_market_hours_entry.json` file (e.g., `usa_market_hours_entry.json`).
2. Open it and copy its entire JSON content.
3. Locate LEAN's main market hours database file. This is typically found at:

   ```
   [YourLeanProject]/Data/market-hours/market-hours-database.json
   ```

4. Open `market-hours-database.json`.
5. Find the `"entries": { ... }` object.
6. Paste the copied JSON content as a new entry within the `entries` object. If an entry for your market already exists (e.g., `"Equity-usa-*"`), you may want to merge or replace it.
