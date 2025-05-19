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

# Downloading IBKR Data & Converting to LEAN Format

This section details how to download 1-minute historical data from Interactive Brokers (IBKR) and convert it into the Lean-compatible format for local backtesting. It covers both trade and quote data.

## Prerequisites
- **Python 3.11** installed.
- **Required Python Libraries:**
  - `ibapi` (Interactive Brokers API client)
  - `pandas`
  - `pytz`

  Install them with:
  ```bash
  pip install ibapi pandas pytz
  ```

- **Interactive Brokers Account**: Requires market data subscriptions for desired instruments.
- **IBKR Trader Workstation (TWS) or IB Gateway**: Must be running with API access enabled:
  - In TWS/Gateway: Go to **File > Global Configuration > API > Settings**.
  - Check "Enable ActiveX and Socket Clients".
  - Note the "Socket port" (e.g., 7497 for TWS, 4002 for Gateway).
  - Optionally, add `127.0.0.1` to "Trusted IP Addresses" if running locally.
- **QuantConnect LEAN Engine**: Assumes Lean is set up as per the above section.

---

## Part 1: Downloading 1-Minute Data from IBKR
Uses the Python script `download_ibkr_data.py`.

### Step 1.1: Prepare Configuration Files
Create a file named `datadownload_config.json` in the same directory as `download_ibkr_data.py`. This file specifies download parameters.

**Example `datadownload_config.json` for trade data:**
```json
{
  "contract_details": {
    "symbol": "AAPL",
    "secType": "STK",
    "exchange": "SMART",
    "currency": "USD"
  },
  "instrument_timezone": "America/New_York",
  "date_range": {
    "start_year": 2023,
    "start_month": 12,
    "start_day": 1,
    "end_year": 2023,
    "end_month": 12,
    "end_day": 15
  },
  "api_settings": {
    "ibkr_port": 7497,
    "client_id": 1,
    "max_active_requests": 5,
    "inter_request_delay_seconds": 3,
    "max_connect_retries": 5,
    "connect_retry_delay_seconds": 30,
    "max_request_retries": 3,
    "request_retries_delay_seconds": 20
  },
  "output": {
    "filename_template": "{symbol}_1min_{start_year}-{end_year}_utc.csv"
  }
}
```

**To download quote data (bid/ask) instead of trade data, include the following in your `datadownload_config.json`:**

```json
"download_settings": {
  "download_type": "BID_ASK"
}
```

This setting instructs the script to request bid/ask data from IBKR. The resulting CSV file will contain columns such as `bid_open`, `bid_high`, `ask_low`, and `ask_close`, and the output filename will reflect the data type (e.g., `AAPL_bid_ask_1min_2023-2023_utc.csv`). Ensure your IBKR account has the appropriate market data subscriptions to access quote data.

**Key Fields:**
- `contract_details`: Defines the instrument (symbol, security type, exchange, currency).
- `instrument_timezone`: Timezone of the instrument (e.g., "America/New_York").
- `date_range`: Start and end dates for data.
- `api_settings.ibkr_port`: Matches your TWS/Gateway port.
- `api_settings.client_id`: Unique ID for this connection.
- `output.filename_template`: Format for the output CSV file.
- `download_settings.download_type`: Set to `"TRADES"` for trade data or `"BID_ASK"` for quote data.

### Step 1.2: Run TWS or IB Gateway
Ensure TWS or IB Gateway is running and logged in, with API settings configured.

> **Note**: IBKR allows only one active session per account at a time. Avoid conflicts by ensuring no other sessions are active elsewhere.

### Step 1.3: Execute the Download Script
Navigate to the directory with `download_ibkr_data.py` and `datadownload_config.json`, then run:
```bash
python download_ibkr_data.py
```

The script will:
- Load settings from `datadownload_config.json`.
- Connect to TWS/Gateway.
- Download 1-minute data (trade or quote based on configuration) in weekly chunks.
- Convert timestamps to UTC.
- Save data to a CSV (e.g., `AAPL_1min_2023-2023_utc.csv` or `AAPL_bid_ask_1min_2023-2023_utc.csv`).
- Resume from the last date if interrupted.

Monitor the console for progress and errors.

---

## Part 2: Converting IBKR Data to LEAN Format
Uses the Python script `convert_ibkr_to_lean.py`.

### Step 2.1: Prepare Configuration File for Conversion
Create `market_config.json` in the same directory as `convert_ibkr_to_lean.py`. This defines market hours and timezones.

**Example `market_config.json`:**
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

**Key Fields:**
- Market key (e.g., `"usa"`): Matches the `-m` argument in the script.
- `timezone`: IANA timezone (e.g., "America/New_York").
- Session times: `preMarketStart`, `marketStart`, etc., in HH:MM:SS format (local time).
- `weekendDays`: List of weekend days (0=Monday, 6=Sunday).

### Step 2.2: Execute the Conversion Script
Run the script with:
```bash
python convert_ibkr_to_lean.py [INPUT_CSV_PATH] [OUTPUT_LEAN_DATA_DIR] [TICKER] -m [MARKET_CODE]
```

**Arguments:**
- `[INPUT_CSV_PATH]`: Path to the downloaded CSV (e.g., `AAPL_1min_2023-2023_utc.csv` or `AAPL_bid_ask_1min_2023-2023_utc.csv`).
- `[OUTPUT_LEAN_DATA_DIR]`: Lean data directory (e.g., `./lean_data_output`).
- `[TICKER]`: Symbol (e.g., `AAPL`); converted to lowercase by the script.
- `-m [MARKET_CODE]`: Market code from `market_config.json` (e.g., `usa`). Defaults to `usa` if omitted.

**Example:**
```bash
python convert_ibkr_to_lean.py AAPL_1min_2023-2023_utc.csv ./lean_data_output aapl -m usa
```

The script will:
1. Read the CSV and `market_config.json`.
2. Convert UTC timestamps to local market time.
3. Shift IBKR timestamps (+1 minute) to match Lean’s end-of-bar convention.
4. Scale prices by 10,000.
5. Generate daily zipped CSV files in:
   ```
   [OUTPUT_LEAN_DATA_DIR]/equity/[MARKET_CODE]/minute/[ticker_lowercase]/[YYYYMMDD]_{type}.zip
   ```
   where `{type}` is `trade` for trade data or `quote` for quote data (e.g., `./lean_data_output/equity/usa/minute/aapl/20230103_trade.zip` or `./lean_data_output/equity/usa/minute/aapl/20230103_quote.zip`).
6. Create a map file in:
   ```
   [OUTPUT_LEAN_DATA_DIR]/equity/[MARKET_CODE]/map_files/
   ```
7. Output a `[market]_market_hours_entry.json` file (e.g., `usa_market_hours_entry.json`).

**Note:** The conversion script automatically detects whether the input CSV contains trade data (with columns `open`, `high`, `low`, `close`, `volume`) or quote data (with columns `bid_open`, `bid_high`, `ask_low`, `ask_close`). It processes the data accordingly and generates LEAN files with `trade` or `quote` suffixes in the filenames.

### Step 2.3: Update LEAN's Market Hours Database (Manual Step)
> **Critical for accurate backtesting.**

Incorporate the generated `[market]_market_hours_entry.json` into Lean’s database:
1. Open the generated file (e.g., `usa_market_hours_entry.json`) and copy its contents.
2. Locate Lean’s `market-hours-database.json`:
   ```
   [YourLeanProject]/Data/market-hours/market-hours-database.json
   ```
3. Open it, find the `"entries"` object, and paste the copied content as a new entry. Merge or replace if an entry exists.

### Step 2.4: Markets Other Than Dow Jones/Nasdaq and India
US and India markets are natively supported. For others (e.g., HKFE), modify Lean’s C# code using `Market.India` as a template. Example for HKFE:

**In `Common/Exchange.cs`:**
```csharp
public static Exchange HKFE { get; } = new("HKFE", "HKFE", "The Hong Kong Stock Exchange", QuantConnect.Market.HKFE, SecurityType.Equity);
```

**In `Common/Global.cs`:**
```csharp
case "HKFE":
    if (market == Market.HKFE)
    {
        return Exchange.HKFE;
    }
    return Exchange.UNKNOWN;
```

**In `Engine/DataFeeds/ApiDataProviders.cs`:**
After the India check, add:
```csharp
else if (!_subscribedToIndiaEquityMapAndFactorFiles && market.Equals(Market.HKFE, StringComparison.InvariantCultureIgnoreCase)
         && (securityType == SecurityType.Equity || securityType == SecurityType.Option || IsAuxiliaryData(filePath)))
{
    throw new ArgumentException("ApiDataProvider(): Must be subscribed to map and factor files to use the ApiDataProvider " +
        "to download HKFE data from QuantConnect. " +
        "Please visit https://www.quantconnect.com/datasets for details.");
}
```

If these changes still cant't enable Lean to recognize HKFE data, you will need to `find in files` and search for `Market.India` to refer to how India market was recognized. <br />
You may want to refer to `main.py` to see if the data can be printed in console.
