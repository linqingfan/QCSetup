# QuantConnect Local Lean Setup

This guide provides instructions to set up QuantConnect Lean to run locally on Windows without using Docker. It assumes you have some familiarity with Visual Studio Code (VS Code).

## Install the Latest .NET
Download and install the latest version of .NET (e.g., .NET 9.0 or newer) from the official website:
[https://dotnet.microsoft.com/en-us/download/dotnet/9.0](https://dotnet.microsoft.com/en-us/download/dotnet/9.0)

## Download QuantConnect LEAN Source Codes
Clone the QuantConnect Lean repository using GitHub Desktop or the command line. Ensure Git is installed on your system:
```bash
git clone https://github.com/QuantConnect/Lean
```

## Fix `debugpy` Bugs on Windows Platform (Non-Docker)
On Windows, the `debugpy` module in QuantConnect may fail to locate the Python executable. This quick fix is specific to Windows and may not be needed on Linux or other platforms.

Modify `AlgorithmFactory/DebuggerHelper.cs` by adding the following lines after `import debugpy`:
```python
import debugpy
import os, sys
python_executable_in_venv = os.path.join(sys.prefix, 'Scripts', 'python.exe')
debugpy.configure(python=python_executable_in_venv)
```

## Set Up a Python Virtual Environment
Set the environment variable **`PYTHONNET_PYDLL`** to the path of your system Python DLL, e.g., `C:\Users\username\AppData\Local\Programs\Python\Python311\python311.dll`.

In the QuantConnect Lean root folder (assuming Python 3.11 is installed), run these commands:
```bash
python -m venv .venv
.venv\Scripts\activate
python.exe -m pip install --upgrade pip
pip install wheel jupyterlab pandas==2.1.4 wrapt==1.16.0 clr_loader==0.1.6 matplotlib debugpy
python -m ipykernel install --user --name="myvenv" --display-name="Python Lean"
pip install --no-cache-dir quantconnect-stubs
```
Afterward, in VS Code, select the `.venv` environment as your Python interpreter.

## Configure `Launcher/config.json`
Edit `Launcher/config.json` to specify your algorithm details. Example:
```json
{
"algorithm-type-name": "BasicTemplateAlgorithm",
"algorithm-language": "Python",
"algorithm-location": "../../../Algorithm.Python/BasicTemplateAlgorithm.py",


"debugging": true,
"debugging-method": "Debugpy",
}
```
- `"algorithm-type-name"`: The class name containing the `Initialize` function.
- `"algorithm-language"`: Set to `"Python"`.
- `"algorithm-location"`: Path to your Python algorithm script.

## Compile QuantConnect
Compile Lean using one of these commands:

**Debug Mode:**
```bash
dotnet build QuantConnect.Lean.sln
```

**Release Mode:**
```bash
dotnet build QuantConnect.Lean.sln -c Release
```
If using Release mode, update all instances of "Debug" to "Release" in related settings (e.g., `.vscode/launch.json`).

## Launching and Debugging
To run and debug a QuantConnect algorithm:

1. In VS Code, open "Run and Debug" (Ctrl+Shift+D).
2. Select the "launch" configuration.
3. Click the "play" icon.
4. Wait for the console to display "Debugger listening on port 5678".
5. Set breakpoints in your Python algorithm code (optional).
6. Select the "Attach to Python" configuration and click play.

No graphical charts are displayed during local debugging; results like Sharpe ratio appear in the console after backtesting.

## Disabling Pre-Launch Build
To skip automatic rebuilding when no changes are made to C# files or `Launcher/config.json`, comment out the `preLaunchTask` in `.vscode/launch.json`:
```json
// "preLaunchTask": "build",
```
If changes are made, manually recompile as described in [Compile QuantConnect](#compile-quantconnect).

## QuantBook Research
To use QuantBook for research:

1. Open a Git Bash terminal (or any shell supporting `.sh` scripts).
2. Navigate to the Lean project root directory.
3. Run the Jupyter Notebook launch script:
```bash
cd .vscode
sh launch_research.sh
```
4. In Jupyter, open `BasicQuantBookTemplate.ipynb` for an example.
5. In the first cell, execute:
```python
%run start.py
```

---
# Downloading IBKR Data & Converting to LEAN Format

This section details how to download 1-minute historical data from Interactive Brokers (IBKR) using the `download_ibkr_data.py` script and then convert it into the Lean-compatible format for local backtesting using `convert_ibkr_to_lean.py`.

## Prerequisites
- **Python 3.11 (or compatible)** installed and configured in a virtual environment.
- **Required Python Libraries:**
- `ibapi` (Interactive Brokers API client)
- `pandas`
- `pytz`
Install them into your activated virtual environment:
```bash
pip install ibapi pandas pytz
```
- **Interactive Brokers Account**: Requires market data subscriptions for the desired instruments and data types (e.g., Level 1 for trades, Level 1 for quotes).
- **IBKR Trader Workstation (TWS) or IB Gateway**:
- Must be running and logged in.
- API access enabled: In TWS/Gateway, go to **File > Global Configuration > API > Settings** (or **Edit > Global Configuration...** depending on version).
- Check "Enable ActiveX and Socket Clients".
- Note the "Socket port" (e.g., `7497` for paper TWS, `7496` for live TWS, `4002` for paper Gateway, `4001` for live Gateway).
- Optionally, add `127.0.0.1` to "Trusted IP Addresses" if running the script on the same machine.
- **QuantConnect LEAN Engine**: Assumes Lean is set up as per the "QuantConnect Local Lean Setup" section.

---

## Part 1: Downloading 1-Minute Data from IBKR
This part uses the Python script `download_ibkr_data.py`.

### Step 1.1: Prepare Configuration File (`datadownload_config.json`)
Create a JSON file named `datadownload_config.json` in the same directory as `download_ibkr_data.py`. This file specifies all parameters for the data download.

**Example `datadownload_config.json` for Equity Trades (AAPL):**
```json
{
"contract_details": {
"symbol": "AAPL",
"secType": "STK",
"exchange": "SMART",
"currency": "USD",
"primaryExchange": "NASDAQ" // Optional, but good for US stocks to resolve ambiguity
},
"instrument_timezone": "America/New_York", // Timezone of the exchange where the instrument primarily trades
"date_range": { // Defines the overall period for which data is desired
"start_year": 2023,
"start_month": 1,
"start_day": 1,
"end_year": 2023,
"end_month": 12,
"end_day": 31
},
"api_settings": {
"ibkr_port": 7497, // Match your TWS/Gateway paper trading port (7496 for live)
"client_id": 10, // Any unique integer for this script instance
"max_active_requests": 3, // Max concurrent data requests to IBKR (e.g., 2-5)
"inter_request_delay_seconds": 11, // Delay (seconds) between sending new requests. Crucial for avoiding pacing violations.
"max_connect_retries": 5,
"connect_retry_delay_seconds": 30,
"max_request_retries": 3, // Retries for individual data chunk requests
"request_retry_delay_seconds": 20, // Delay before retrying a failed data chunk request
"use_rth": 1, // 1 for Regular Trading Hours only (common for stocks), 0 for all hours (crypto/futures or if pre/post market is needed)
"what_to_show": "TRADES" // Data type: TRADES, AGGTRADES, MIDPOINT, BID, ASK, BID_ASK
},
"output": {
// Filename template. Available placeholders: {symbol}, {what_to_show_lowercase}, {secType_lowercase}, {start_year}, {end_year}
"filename_template": "{symbol}_{what_to_show_lowercase}_1min_{start_year}-{end_year}_utc.csv"
}
}
```

**Key Configuration Fields for `datadownload_config.json`:**

- **`contract_details`**: Defines the financial instrument.
- `symbol`: Ticker symbol (e.g., "AAPL", "ES", "EUR", "BTC").
- `secType`: Security Type (e.g., "STK", "FUT", "CASH", "CRYPTO", "IND").
- `exchange`: Destination exchange. Use "SMART" for stocks. For specific exchanges like "CME" (futures), "IDEALPRO" (forex), "PAXOS" (crypto), specify directly.
- `currency`: Currency of the instrument (e.g., "USD", "EUR", "HKD").
- `primaryExchange`: (Optional, mainly for STK) Helps disambiguate stocks. E.g., "NASDAQ", "NYSE", "ARCA".
- `lastTradeDateOrContractMonth`: (Required for FUT/OPT) Format "YYYYMM" or "YYYYMMDD".
- `multiplier`: (For FUT/OPT) Contract multiplier.
- `right`: (For OPT) "C" for Call, "P" for Put.
- `strike`: (For OPT) Option strike price.
- **`instrument_timezone`**: IANA timezone string for the instrument's primary trading exchange (e.g., "America/New_York", "UTC" for crypto).
- **`date_range`**: Specifies the overall period for data download.
- `start_year`, `start_month`, `start_day`: Beginning of the range. **Be realistic about historical data availability for 1-minute bars (e.g., for BTC on PAXOS, data might only be available from 2020/2021 onwards, not 2018).**
- `end_year`, `end_month`, `end_day`: End of the range (inclusive).
- **`api_settings`**: Controls connection and request behavior.
- `ibkr_port`: Socket port of your running TWS or IB Gateway.
- `client_id`: A unique integer ID for this API client connection.
- `max_active_requests`: Maximum number of concurrent historical data requests. IBKR has limits (e.g., 60 requests per 60 seconds). A lower number like 2-5 is safer.
- `inter_request_delay_seconds`: Pause between initiating new data requests. Increase this (e.g., 10-20 seconds) if you encounter pacing violations (Error 162).
- `max_connect_retries`, `connect_retry_delay_seconds`: For initial connection attempts.
- `max_request_retries`, `request_retry_delay_seconds`: For retrying individual data requests that fail.
- `use_rth`:
- `1`: Request data only within Regular Trading Hours (RTH).
- `0`: Request all available data, including outside RTH. **Essential for crypto and futures**, or if you need pre/post-market stock data.
- `what_to_show`: Type of data to download.
- `"TRADES"`: Individual last trade data (common for stocks).
- `"AGGTRADES"`: Aggregated trades. **Often required for crypto like BTC on PAXOS (Error 10299 if `TRADES` is used).**
- `"MIDPOINT"`: Midpoint of bid/ask.
- `"BID"`: Bid prices.
- `"ASK"`: Ask prices.
- `"BID_ASK"`: Both bid and ask data. If selected, `download_ibkr_data.py` must be equipped to handle and store separate bid/ask columns (e.g., `bid_open`, `ask_open`, etc.). The output filename might reflect this.
- **`output`**:
- `filename_template`: A Python f-string like template for the output CSV filename. Available placeholders: `{symbol}`, `{what_to_show_lowercase}`, `{secType_lowercase}`, `{start_year}`, `{end_year}`.

**Example `datadownload_config.json` for BTC (Crypto - AGGTRADES):**
```json
{
"contract_details": {
"symbol": "BTC",
"secType": "CRYPTO",
"exchange": "PAXOS", // For BTCUSD via IBKR
"currency": "USD"
},
"instrument_timezone": "UTC",
"date_range": {
"start_year": 2021, // << Adjust to a realistic start year for 1-min PAXOS BTC data
"start_month": 1,
"start_day": 1,
"end_year": 2023,
"end_month": 12,
"end_day": 31
},
"api_settings": {
"ibkr_port": 4002, // Example: IB Gateway paper trading port
"client_id": 11,
"max_active_requests": 2,
"inter_request_delay_seconds": 16, // Increased delay for crypto
"use_rth": 0, // MUST be 0 for crypto
"what_to_show": "AGGTRADES" // Required for BTC on PAXOS
},
"output": {
"filename_template": "{symbol}_{secType_lowercase}_{what_to_show_lowercase}_1min_{start_year}-{end_year}_utc.csv"
}
}
```

**Important Notes for `datadownload_config.json`:**
- **Data Availability (Error 162 - "HMDS query returned no data"):** IBKR's historical data depth for 1-minute bars varies significantly. For some instruments (especially crypto like BTC on PAXOS), 1-minute data may only be available for the last few years (e.g., starting from 2020, 2021, or even later). **If you get Error 162 consistently, your `date_range.start_year` is likely too early.** Test with a very recent `start_year` first (e.g., last year or current year).
- **Pacing Violations (Error 162 - can also be pacing):** Requesting too much data too quickly can lead to temporary blocks. If Error 162 persists even for recent dates, try increasing `inter_request_delay_seconds` (e.g., to 20-30s) and reducing `max_active_requests` (e.g., to 1 or 2).
- **`what_to_show` for `BID_ASK`:** If you set `what_to_show` to `"BID_ASK"`, the `download_ibkr_data.py` script must be specifically designed to handle this. It would typically make two separate requests (one for `BID`, one for `ASK`) or parse a combined stream if IBKR provides one. The output CSV would then contain columns like `bid_open`, `bid_high`, `bid_low`, `bid_close`, and `ask_open`, `ask_high`, `ask_low`, `ask_close`. The `convert_ibkr_to_lean.py` script would also need to recognize these columns to generate Lean `quote` files.

### Step 1.2: Run TWS or IB Gateway
Ensure your Trader Workstation (TWS) or IB Gateway application is:
- Running on your machine.
- Logged into your IBKR account (live or paper).
- API settings are correctly configured (as per Prerequisites).

> **Note**: IBKR typically allows only one active API connection per client ID and often one TWS/Gateway session per user at a time.

### Step 1.3: Execute the Download Script
1. Open a terminal or command prompt.
2. Activate your Python virtual environment (e.g., `.venv\Scripts\activate`).
3. Navigate to the directory containing `download_ibkr_data.py` and your `datadownload_config.json`.
4. Run the script:
```bash
python download_ibkr_data.py
```

The script will:
- Load settings from `datadownload_config.json`.
- Connect to TWS/Gateway.
- Request historical 1-minute data in chunks (the script internally uses a `durationStr`, e.g., "1 W" or "1 D", for each chunk request).
- Convert timestamps to UTC.
- Save data to a CSV file named according to `output.filename_template`.
- Attempt to resume from the last date if interrupted.

Monitor the console for progress, errors (like Code 162 or 10299), and connection status.

---

## Part 2: Converting IBKR Data to LEAN Format
Uses the Python script `convert_ibkr_to_lean.py`.

### Step 2.1: Prepare Configuration File for Conversion (`market_config.json`)
Create `market_config.json` in the same directory as `convert_ibkr_to_lean.py`. This defines market hours and timezones.

**Example `market_config.json`:**
```json
{
"usa_stk": {
"timezone": "America/New_York",
"marketStart": "09:30:00",
"marketEnd": "16:00:00",
"weekendDays": [5, 6]
},
"paxos_crypto": {
"timezone": "UTC",
"marketStart": "00:00:00",
"marketEnd": "23:59:59",
"weekendDays": []
}
}
```
*(Ensure market codes like `usa_stk` and `paxos_crypto` are used consistently with the `-m` argument in the conversion script.)*

### Step 2.2: Execute the Conversion Script
Run the script with appropriate arguments. The script needs to know if it's processing trade data or quote data, usually by inspecting the columns in the input CSV.

```bash
python convert_ibkr_to_lean.py [INPUT_CSV_PATH] [OUTPUT_LEAN_DATA_DIR] [TICKER] -m [MARKET_CODE] -s [SEC_TYPE_FOR_LEAN_DIR]
```

**Arguments:**
- `[INPUT_CSV_PATH]`: Path to the CSV from `download_ibkr_data.py`.
- `[OUTPUT_LEAN_DATA_DIR]`: Lean data directory (e.g., `./data`).
- `[TICKER]`: Symbol for Lean (e.g., `aapl`, `btcusd`).
- `-m [MARKET_CODE]`: Market code from `market_config.json` (e.g., `usa_stk`, `paxos_crypto`).
- `-s [SEC_TYPE_FOR_LEAN_DIR]`: Security type for Lean's directory structure (e.g., `equity`, `crypto`, `future`).

**Example (AAPL - Trades):**
```bash
python convert_ibkr_to_lean.py AAPL_stk_trades_1min_2023-2023_utc.csv ./data equity aapl -m usa_stk
```
**Example (BTC - AggTrades, treated as trades for Lean):**
```bash
python convert_ibkr_to_lean.py BTC_crypto_aggtrades_1min_2021-2023_utc.csv ./data crypto btcusd -m paxos_crypto
```

The script should:
1. Read CSV and `market_config.json`.
2. Convert UTC timestamps to local market time, adjusting for Lean's end-of-bar convention (+1 minute for 1-min bars).
3. Scale prices by 10,000 for equities (other asset classes might differ).
4. Generate daily zipped CSV files in LEAN format:
`[OUTPUT_LEAN_DATA_DIR]/[sec_type_for_lean_dir]/[market_code]/minute/[ticker_lowercase]/[YYYYMMDD]_{type}.zip`
(where `{type}` is `trade` or `quote` based on CSV content).
5. Create map files if applicable.
6. Output a `[market]_market_hours_entry.json` snippet.

### Step 2.3: Update LEAN's Market Hours Database (Manual Step)
> **Critical for accurate backtesting.**

Incorporate the generated `[market]_market_hours_entry.json` into Lean’s database:
1. Open the generated file (e.g., `usa_stk_market_hours_entry.json`) and copy its contents.
2. Locate Lean’s `market-hours-database.json`:
`[YourLeanProject]/Data/market-hours/market-hours-database.json`
3. Open it, find the `"entries"` object, and paste the copied content as a new entry, ensuring the market key (e.g., "usa", "paxos") matches how Lean will identify this market.

### Step 2.4: Markets Other Than Dow Jones/Nasdaq and India
For markets not natively detailed in Lean (e.g., HKFE), C# code modifications might be needed as outlined in the original document, using `Market.India` as a template. Focus on `Market.cs`, `Exchange.cs`, and `Global.cs` (or similar mapping files).

If these changes still can't enable Lean to recognize HKFE data, you will need to `find in files` and search for `Market.India` to refer to how India market was recognized.
You may want to refer to `main.py` to see if the data can be printed in console.
