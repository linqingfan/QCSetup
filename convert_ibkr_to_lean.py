import pandas as pd
import pytz
import os
import zipfile
from pathlib import Path
import argparse
from collections import Counter
import datetime
import json
import numpy as np

def convert_ibkr_to_lean(input_csv_path, output_data_folder, ticker, market="hkfe"):
    """
    Converts an IBKR 1-minute data CSV (UTC timestamps) to LEAN format
    (daily zipped CSVs with appropriate timestamps and data types for equity, crypto, or forex)
    and generates a market hours JSON entry.

    Args:
        input_csv_path (str): Path to the input CSV file.
        output_data_folder (str): The root LEAN data folder.
        ticker (str): The stock/crypto/forex pair ticker symbol.
        market (str): The market code used by LEAN (e.g., hkfe, binance, oanda).
    """
    print(f"Starting conversion for {ticker} (market: {market}) from {input_csv_path}...")

    # --- Load Market Configuration FIRST ---
    config_path = Path(__file__).parent / "market_config.json"
    market_config_json = None
    target_tz_str = None
    target_tz = None
    security_type = None
    standard_market_start_str = None
    standard_market_end_str = None
    configured_weekend_days_py = set()
    standard_market_start_time = None
    standard_market_end_time = None

    try:
        with open(config_path, 'r') as f:
            market_configs_all = json.load(f)
        if market not in market_configs_all:
            print(f"Error: Market '{market}' not found in {config_path}. Please add its configuration.")
            return
        market_config_json = market_configs_all[market]
        print(f"Loaded configuration for market '{market}' from {config_path}")

        security_type = market_config_json.get("security_type")
        if not security_type:
            print(f"Error: 'security_type' (e.g., 'equity', 'crypto', 'forex') not defined for market '{market}' in {config_path}.")
            return
        security_type = security_type.lower()
        print(f"Determined security type for LEAN: {security_type}")

        if security_type in ["crypto", "forex"]:
            target_tz_str = market_config_json.get("timezone", "UTC")
            standard_market_start_str = market_config_json.get("marketStart", "00:00:00")
            standard_market_end_str = market_config_json.get("marketEnd", "23:59:59")
            default_weekends = [] if security_type == "crypto" else [5, 6]
            configured_weekend_days_py = set(market_config_json.get("weekendDays", default_weekends))
            print(f"{security_type.capitalize()} market type. TZ: {target_tz_str}. "
                  f"Market hours for derivation: {standard_market_start_str}-{standard_market_end_str}. "
                  f"Configured weekend days (0=Mon, 6=Sun): {configured_weekend_days_py or 'None (trades all days)'}")
        elif security_type == "equity":
            target_tz_str = market_config_json.get("timezone")
            if not target_tz_str:
                print(f"Error: 'timezone' must be defined for '{security_type}' market '{market}' in {config_path}")
                return
            essential_session_keys = ["marketStart", "marketEnd", "weekendDays"]
            for key in essential_session_keys:
                if key not in market_config_json:
                    print(f"Error: Essential key '{key}' not found for '{security_type}' market '{market}' in {config_path}.")
                    return
            standard_market_start_str = market_config_json["marketStart"]
            standard_market_end_str = market_config_json["marketEnd"]
            configured_weekend_days_py = set(market_config_json["weekendDays"])
            print(f"{security_type.capitalize()} market type. TZ: {target_tz_str}. "
                  f"Market hours for derivation: {standard_market_start_str}-{standard_market_end_str}. "
                  f"Configured weekend days (0=Mon, 6=Sun): {configured_weekend_days_py}")
        else:
            print(f"Error: Unsupported security_type '{security_type}' for market '{market}'. Please configure or add support.")
            return

        try:
            target_tz = pytz.timezone(target_tz_str)
        except pytz.exceptions.UnknownTimeZoneError:
            print(f"Error: Unknown timezone '{target_tz_str}' specified for market '{market}'.")
            return
        try:
            standard_market_start_time = datetime.datetime.strptime(standard_market_start_str, "%H:%M:%S").time()
            standard_market_end_time = datetime.datetime.strptime(standard_market_end_str, "%H:%M:%S").time()
        except ValueError:
            print(f"Error: Invalid time format in 'marketStart' ('{standard_market_start_str}') or 'marketEnd' ('{standard_market_end_str}') "
                  f"for market '{market}'. Expected HH:MM:SS.")
            return
    except FileNotFoundError:
        print(f"Error: Market configuration file not found at {config_path}")
        return
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {config_path}")
        return
    except Exception as e:
        print(f"Error loading market configuration: {e}")
        return

    # --- Read Input CSV and Prepare Initial DataFrame ---
    df = pd.DataFrame() # Initialize df as an empty DataFrame
    lean_data_column_names = [] # To store the standard LEAN names for data columns
    data_type_suffix = "quote" if "quote" in input_csv_path.lower() else "trade"
    print(f"Input data type identified as: {data_type_suffix} (from filename: {Path(input_csv_path).name})")

    try:
        df_raw = pd.read_csv(input_csv_path)
        if df_raw.empty:
            print(f"Warning: Input file {input_csv_path} is empty. No data to process.")
            # df remains empty
        else:
            print(f"Read {len(df_raw)} rows from {input_csv_path}")

            expected_num_cols = 0
            start_data_col_index = 1 # Data columns start from the second column (index 1)

            if data_type_suffix == "trade":
                expected_num_cols = 6 # 1 date + 5 data (OHLCV)
                lean_data_column_names = ['open', 'high', 'low', 'close', 'volume']
            elif data_type_suffix == "quote":
                expected_num_cols = 11 # 1 date + 10 data (BidOHLCV, AskOHLCV)
                lean_data_column_names = [
                    'bid_open', 'bid_high', 'bid_low', 'bid_close', 'bid_size',
                    'ask_open', 'ask_high', 'ask_low', 'ask_close', 'ask_size'
                ]
            else: # Should not be reached if filename implies trade or quote
                print(f"Critical Error: Could not determine data type (trade/quote) from filename {input_csv_path} for column processing.")
                return

            if len(df_raw.columns) != expected_num_cols:
                print(f"Error: Input {data_type_suffix.upper()} CSV {input_csv_path} has {len(df_raw.columns)} columns. Expected {expected_num_cols}.")
                print(f"Original column headers: {df_raw.columns.tolist()}")
                return

            # Extract and rename date column
            date_column_original_name = df_raw.columns[0]
            df_date_col = df_raw[[date_column_original_name]].copy()
            df_date_col.rename(columns={date_column_original_name: 'date'}, inplace=True)
            df_date_col['date'] = pd.to_datetime(df_date_col['date'], utc=True)
            print(f"Interpreted first column '{date_column_original_name}' as 'date' and parsed as UTC datetime.")

            # Extract data columns by position and assign LEAN names
            df_data_cols = df_raw.iloc[:, start_data_col_index : start_data_col_index + len(lean_data_column_names)].copy()
            df_data_cols.columns = lean_data_column_names
            print(f"Assigned standard LEAN names to data columns {start_data_col_index} through {start_data_col_index + len(lean_data_column_names)-1}: {lean_data_column_names}")

            # Combine into the main DataFrame 'df'
            df = pd.concat([df_date_col, df_data_cols], axis=1)

            # --- START OF NaN HANDLING MODIFICATION ---
            print("\n--- NaN Check and Handling ---")
            cols_to_check_for_nans = lean_data_column_names # All data columns

            for col_name in cols_to_check_for_nans:
                if col_name in df.columns:
                    nan_count = df[col_name].isnull().sum()
                    if nan_count > 0:
                        print(f"Column '{col_name}' has {nan_count} NaN values.")

                        # Strategy 1: Fill Bid from Ask (for quote data)
                        if data_type_suffix == "quote" and col_name.startswith("bid_"):
                            corresponding_ask_col = "ask_" + col_name[len("bid_"):]
                            if corresponding_ask_col in df.columns:
                                print(f"  Attempting to fill NaNs in '{col_name}' from '{corresponding_ask_col}'...")
                                df[col_name] = df[col_name].fillna(df[corresponding_ask_col])
                                nans_after_ask_fill = df[col_name].isnull().sum()
                                if nans_after_ask_fill < nan_count:
                                    print(f"    Filled {nan_count - nans_after_ask_fill} NaNs using '{corresponding_ask_col}'. Remaining NaNs: {nans_after_ask_fill}")
                                if nans_after_ask_fill == 0:
                                    continue # Skip to next column if all NaNs filled
                                nan_count = nans_after_ask_fill # Update nan_count for subsequent fills

                        # Strategy 2: Forward Fill (ffill) for price-like columns
                        # Apply to OHLC, BidOHLC, AskOHLC.
                        # Be cautious with volume/size - ffill might not always be appropriate.
                        # For simplicity here, applying to all non-volume/size columns if NaNs persist.
                        is_price_col = not col_name.endswith("_volume") and not col_name.endswith("_size")
                        if is_price_col and nan_count > 0:
                            print(f"  Attempting to forward fill (ffill) remaining NaNs in price-like column '{col_name}'...")
                            df[col_name] = df[col_name].ffill()
                            nans_after_ffill = df[col_name].isnull().sum()
                            if nans_after_ffill < nan_count:
                                print(f"    Forward-filled {nan_count - nans_after_ffill} NaNs. Remaining NaNs: {nans_after_ffill}")
                            if nans_after_ffill == 0:
                                continue
                            nan_count = nans_after_ffill

                        # Strategy 3: Fill with 0 for volume/size columns, or if ffill didn't clear all NaNs for prices
                        if nan_count > 0: # If NaNs still exist
                            fill_value = 0
                            # For price columns, if still NaN after ffill, it means they are at the beginning of the DataFrame.
                            # Filling with 0 might be a last resort, or you might choose to drop these rows.
                            # For this script, we'll fill with 0 as per previous logic implicitly handling this.
                            if is_price_col:
                                print(f"  Warning: NaNs remain in price-like column '{col_name}' after ffill (likely at start of data). Filling with {fill_value}.")
                            else: # volume or size
                                print(f"  Filling remaining NaNs in volume/size column '{col_name}' with {fill_value}.")

                            df[col_name] = df[col_name].fillna(fill_value)
                            nans_after_zero_fill = df[col_name].isnull().sum()
                            if nans_after_zero_fill < nan_count:
                                print(f"    Filled {nan_count - nans_after_zero_fill} NaNs with {fill_value}. Remaining NaNs: {nans_after_zero_fill}")
                            # At this point, if nans_after_zero_fill is still > 0, it's an issue for astype(int) later if applicable.
                            # However, .astype(int) for scaled equity prices or forex/equity volumes happens later and would fail
                            # if NaNs persist. This ensures they are 0.
            print("--- End of NaN Handling ---\n")
            # --- END OF NaN HANDLING MODIFICATION ---


    except FileNotFoundError:
        print(f"Error: Input file not found at {input_csv_path}")
        return
    except pd.errors.EmptyDataError:
        print(f"Warning: Input file {input_csv_path} is empty or contains no data. No data to process.")
        # df remains empty
    except Exception as e:
        print(f"Error reading or processing CSV: {e}")
        return

    if not df.empty:
        df['local_datetime_start'] = df['date'].dt.tz_convert(target_tz)
    else:
        df['local_datetime_start'] = pd.Series(dtype=f'datetime64[ns, {target_tz.zone if target_tz else "UTC"}]')

    # --- Analyze Holidays and Early Closes ---
    trading_dates = set()
    holidays_derived = []
    early_closes_derived = {}

    if not df.empty and 'local_datetime_start' in df.columns and not df['local_datetime_start'].dropna().empty:
        daily_groups = df.groupby(df['local_datetime_start'].dt.date)
        for date_obj, group in daily_groups:
            trading_dates.add(date_obj)
            actual_day_end_dt = group['local_datetime_start'].max() + pd.Timedelta(minutes=1)
            actual_day_end_time = actual_day_end_dt.time()
            is_potentially_full_day_config = (standard_market_start_time == datetime.time(0, 0, 0) and
                                              standard_market_end_time == datetime.time(23, 59, 59))
            if is_potentially_full_day_config and actual_day_end_time == datetime.time(0, 0, 0): # Wrapped around midnight for 24h market
                pass
            elif actual_day_end_time < standard_market_end_time:
                date_key = f"{date_obj.month}/{date_obj.day}/{date_obj.year}"
                early_closes_derived[date_key] = actual_day_end_time.strftime("%H:%M:%S")

        if not df['local_datetime_start'].dropna().empty:
            all_dates_in_range = set()
            start_date_data = df['local_datetime_start'].min().date()
            end_date_data = df['local_datetime_start'].max().date()
            current_date_iter = start_date_data
            while current_date_iter <= end_date_data:
                all_dates_in_range.add(current_date_iter)
                current_date_iter += datetime.timedelta(days=1)
            missing_dates = sorted(list(all_dates_in_range - trading_dates))
            for missing_date in missing_dates:
                if missing_date.weekday() not in configured_weekend_days_py:
                    holidays_derived.append(missing_date)
        else:
            print("Warning: 'local_datetime_start' is empty after processing. Cannot derive holidays from data range.")
    else:
        print("Warning: Input DataFrame is empty or 'local_datetime_start' could not be populated. Cannot analyze holidays or early closes from data.")

    holidays_json_format = [f"{d.month}/{d.day}/{d.year}" for d in holidays_derived]

    # --- Determine first date for Map File ---
    first_date_obj_for_map = None
    if not df.empty:
        try:
            if 'local_datetime_start' in df.columns and not df['local_datetime_start'].dropna().empty:
                first_date_obj_for_map = df['local_datetime_start'].min().date()
            elif 'date' in df.columns and not df['date'].dropna().empty:
                print("Warning: 'local_datetime_start' is empty. Falling back to 'date' column for map file start date.")
                first_date_obj_for_map = df['date'].min().dt.tz_convert(target_tz).date()
            else:
                print("Warning: Could not determine first date for map file from 'local_datetime_start' or 'date' columns.")
        except Exception as e:
            print(f"Warning: Error determining first date for map file: {e}")
    else:
        print("Input DataFrame is empty. Cannot determine first date for map file.")

    # --- Generate JSON structure for market-hours-database.json ---
    sessions_json = {}
    py_to_json_dow_map = {0: "monday", 1: "tuesday", 2: "wednesday", 3: "thursday", 4: "friday", 5: "saturday", 6: "sunday"}
    json_mh_db_sec_type = security_type.capitalize()
    market_hours_entry_key = f"{json_mh_db_sec_type}-{market}-[*]"
    _json_market_start = market_config_json.get("marketStart")
    _json_market_end = market_config_json.get("marketEnd")
    if security_type in ["crypto", "forex"]:
        if _json_market_start is None: _json_market_start = "00:00:00"
        if _json_market_end is None: _json_market_end = "23:59:59"
    elif _json_market_start is None or _json_market_end is None:
        print(f"Error: 'marketStart' or 'marketEnd' missing for {security_type} market '{market}' config and are required for JSON sessions.")
        return
    _json_pre_market_start = market_config_json.get("preMarketStart")
    _json_pre_market_end = market_config_json.get("preMarketEnd")
    _json_post_market_start = market_config_json.get("postMarketStart")
    _json_post_market_end = market_config_json.get("postMarketEnd")
    for py_dow in range(7):
        json_dow_name = py_to_json_dow_map[py_dow]
        if py_dow in configured_weekend_days_py:
            sessions_json[json_dow_name] = []
        else:
            day_sessions = []
            if _json_pre_market_start and _json_pre_market_end:
                day_sessions.append({"start": _json_pre_market_start, "end": _json_pre_market_end, "state": "premarket"})
            if _json_market_start and _json_market_end:
                day_sessions.append({"start": _json_market_start, "end": _json_market_end, "state": "market"})
            else:
                 print(f"Warning: Market start/end times not available for {json_dow_name} for market {market} in sessions JSON.")
            if _json_post_market_start and _json_post_market_end:
                day_sessions.append({"start": _json_post_market_start, "end": _json_post_market_end, "state": "postmarket"})
            sessions_json[json_dow_name] = day_sessions
    market_hours_entry = {
        market_hours_entry_key: {
            "dataTimeZone": target_tz_str, "exchangeTimeZone": target_tz_str,
            "sessions": sessions_json, "holidays": holidays_json_format,
            "earlyCloses": early_closes_derived
        }
    }
    json_entry_filename = f"{market}_{security_type}_market_hours_entry.json"
    script_dir = Path(__file__).parent
    json_output_path = script_dir / json_entry_filename
    try:
        with open(json_output_path, 'w') as f:
            json.dump(market_hours_entry, f, indent=2)
        print(f"\nSaved market hours JSON entry to: {json_output_path}")
        print(f"IMPORTANT: Manually copy the content of this file and merge/replace the entry for '{market_hours_entry_key}' in 'data/market-hours/market-hours-database.json'.")
    except Exception as e:
        print(f"\nError writing market hours JSON entry file: {e}")

    # --- Data Conversion for LEAN ---
    if df.empty:
        print("Skipping LEAN data file generation as input DataFrame is empty.")
        return
    if 'local_datetime_start' not in df.columns or df['local_datetime_start'].dropna().empty:
        print("Error: 'local_datetime_start' column is missing or empty before LEAN data conversion. Cannot proceed.")
        return

    df['local_datetime_end'] = df['local_datetime_start'] + pd.Timedelta(minutes=1)
    midnight_in_data_tz = df['local_datetime_end'].dt.normalize()
    time_since_midnight_in_data_tz = df['local_datetime_end'] - midnight_in_data_tz
    df['time_ms'] = (time_since_midnight_in_data_tz.dt.total_seconds() * 1000).astype(int)

    # `lean_data_column_names` was set during CSV reading based on data_type_suffix
    # `df` already contains 'date' and the correctly named data columns
    df_lean_data_cols = df[lean_data_column_names].copy() # NaNs should have been handled in df already
    df_lean = pd.concat([df[['time_ms']].reset_index(drop=True), df_lean_data_cols.reset_index(drop=True)], axis=1)

    price_type_desc = ""
    volume_or_size_type_desc = ""

    if data_type_suffix == "trade":
        # `lean_data_column_names` for trade is ['open', 'high', 'low', 'close', 'volume']
        trade_price_cols = ['open', 'high', 'low', 'close'] # These are keys in df_lean

        if security_type == "crypto":
            for col in trade_price_cols:
                df_lean[col] = df_lean[col].astype(float)
            df_lean['volume'] = df_lean['volume'].astype(float)
            price_type_desc = "float"
            volume_or_size_type_desc = "float"
        elif security_type == "forex":
            for col in trade_price_cols:
                df_lean[col] = df_lean[col].astype(float)
            df_lean['volume'] = df_lean['volume'].astype(int) # NaNs already filled with 0
            price_type_desc = "float"
            volume_or_size_type_desc = "int"
        elif security_type == "equity":
            scale_prices = market_config_json.get("scale_prices_by_10000", True)
            if scale_prices:
                for col in trade_price_cols:
                    df_lean[col] = (df_lean[col] * 10000).round().astype(int) # NaNs already filled with 0
                price_type_desc = "int (scaled by 10000)"
            else:
                for col in trade_price_cols:
                    df_lean[col] = df_lean[col].astype(float)
                price_type_desc = "float"
            df_lean['volume'] = df_lean['volume'].astype(int) # NaNs already filled with 0
            volume_or_size_type_desc = "int"
        else:
            print(f"Warning: Data type conversion for TRADE security_type '{security_type}' is not explicitly defined. Defaulting to float prices and int volume.")
            for col in trade_price_cols:
                df_lean[col] = df_lean[col].astype(float)
            df_lean['volume'] = df_lean['volume'].astype(int) # NaNs already filled with 0
            price_type_desc = "float (default)"
            volume_or_size_type_desc = "int (default)"

        print(f"Prepared final TRADE DataFrame for {security_type} (market: {market}): "
              f"time_ms (int), ohlc ({price_type_desc}), volume ({volume_or_size_type_desc})")

    elif data_type_suffix == "quote":
        # `lean_data_column_names` for quote includes all 10 bid/ask ohlcv/s columns
        lean_bid_price_cols = ['bid_open', 'bid_high', 'bid_low', 'bid_close']
        lean_ask_price_cols = ['ask_open', 'ask_high', 'ask_low', 'ask_close']
        all_lean_price_cols = lean_bid_price_cols + lean_ask_price_cols
        lean_size_cols = ['bid_size', 'ask_size']

        if security_type == "crypto":
            for col in all_lean_price_cols:
                df_lean[col] = df_lean[col].astype(float)
            for col in lean_size_cols:
                df_lean[col] = df_lean[col].astype(float)
            price_type_desc = "float"
            volume_or_size_type_desc = "float"
        elif security_type == "forex":
            for col in all_lean_price_cols:
                df_lean[col] = df_lean[col].astype(float)
            for col in lean_size_cols:
                df_lean[col] = df_lean[col].astype(int) # NaNs already filled with 0
            price_type_desc = "float"
            volume_or_size_type_desc = "int"
        elif security_type == "equity":
            scale_prices = market_config_json.get("scale_prices_by_10000", True)
            if scale_prices:
                for col in all_lean_price_cols:
                    df_lean[col] = (df_lean[col] * 10000).round().astype(int) # NaNs already filled with 0
                price_type_desc = "int (scaled by 10000)"
            else:
                for col in all_lean_price_cols:
                    df_lean[col] = df_lean[col].astype(float)
                price_type_desc = "float"
            for col in lean_size_cols:
                df_lean[col] = df_lean[col].astype(int) # NaNs already filled with 0
            volume_or_size_type_desc = "int"
        else:
            print(f"Warning: Data type conversion for QUOTE security_type '{security_type}' is not explicitly defined. Defaulting to float prices and int sizes.")
            for col in all_lean_price_cols:
                df_lean[col] = df_lean[col].astype(float)
            for col in lean_size_cols:
                df_lean[col] = df_lean[col].astype(int) # NaNs already filled with 0
            price_type_desc = "float (default)"
            volume_or_size_type_desc = "int (default)"

        print(f"Prepared final QUOTE DataFrame for {security_type} (market: {market}): "
              f"time_ms (int), bid/ask ohlc ({price_type_desc}), bid/ask size ({volume_or_size_type_desc})")
    else:
        print(f"Critical Error: Unknown data_type_suffix '{data_type_suffix}' during LEAN data conversion. Aborting.")
        return

    lean_output_dir = Path(output_data_folder) / security_type / market / 'minute' / ticker.lower()
    lean_output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Ensured output directory exists: {lean_output_dir}")

    # Group df_lean by date. The date for grouping comes from the original df's 'local_datetime_end'.
    # Ensure df_lean and df['local_datetime_end'] are aligned (same length and order).
    df_lean_grouped_by_date = df_lean.set_index(df['local_datetime_end'].dt.date)

    for date_obj, group_df in df_lean_grouped_by_date.groupby(level=0):
        date_str = date_obj.strftime('%Y%m%d')
        csv_filename_in_zip = f"{date_str}_{ticker.lower()}_minute_{data_type_suffix}.csv"
        zip_filename = f"{date_str}_{data_type_suffix}.zip"
        zip_filepath = lean_output_dir / zip_filename
        csv_content = group_df.to_csv(index=False, header=False)
        try:
            with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(csv_filename_in_zip, csv_content)
        except Exception as e:
            print(f"  Error creating zip file {zip_filepath}: {e}")

    if first_date_obj_for_map:
        first_date_str = first_date_obj_for_map.strftime('%Y%m%d')
        map_file_dir = Path(output_data_folder) / security_type / market / 'map_files'
        map_file_path = map_file_dir / f"{ticker.lower()}.csv"
        map_content = f"{first_date_str},{ticker.lower()}\n20801231,{ticker.lower()}\n"
        try:
            map_file_dir.mkdir(parents=True, exist_ok=True)
            with open(map_file_path, 'w') as f:
                f.write(map_content)
            print(f"Generated map file: {map_file_path}")
        except Exception as e:
            print(f"Error generating map file {map_file_path}: {e}")
    else:
        print("Skipping map file generation as first date was not determined.")

    print(f"\nConversion complete. LEAN formatted data saved in: {lean_output_dir}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert IBKR 1-min data CSV to LEAN format and generate a market hours JSON entry.")
    parser.add_argument("input_csv", help="Path to the input CSV file (e.g., SYMBOL_1min_trade_YYYY_YYYY.csv). Filename should contain 'trade' or 'quote'.")
    parser.add_argument("output_dir", help="Path to the root LEAN data folder (e.g., ./data)")
    parser.add_argument("ticker", help="Ticker symbol (e.g., 1211, BTCUSD, EURUSD)")
    parser.add_argument("-m", "--market", default="hkfe", help="LEAN market code (e.g., hkfe, usa, binance, oanda). Default: hkfe. Ensure this market is defined with 'security_type' in market_config.json")

    args = parser.parse_args()
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)
    convert_ibkr_to_lean(args.input_csv, args.output_dir, args.ticker, args.market)
