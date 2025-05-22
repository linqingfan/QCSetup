from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import BarData # Explicit import
import datetime
import time
import pandas as pd
import threading
import pytz
import os
import json

# --- Global Variables ---
bar_collection = {} # Stores data and status for each request ID
# config will be loaded in main

# --- Constants ---
RETRYABLE_ERROR_CODES = {1100, 1101, 1102, 1300, 2103, 2105, 2107, 2150}
REQUEST_SPECIFIC_RETRYABLE_ERROR_CODES = {
    162,
    321,
    322,
    366,
}
INFORMATIONAL_API_CODES = {
    2100, 2104, 2106, 2108, 2158, 2174, 10314
}
CSV_WRITE_ERROR_CODE = 999


class IBapi(EWrapper, EClient):
    def __init__(self, filename: str, expected_timezone_str: str = "America/New_York"):
        EClient.__init__(self, self)
        self.req_id_counter = 0
        self.filename = filename
        try:
            self.expected_timezone = pytz.timezone(expected_timezone_str)
            print(f"IBapi initialized. Timestamps from IBKR without explicit TZ will be interpreted as '{self.expected_timezone}'.")
        except pytz.exceptions.UnknownTimeZoneError:
            print(f"Warning: Unknown timezone '{expected_timezone_str}' in IBapi init. Defaulting to UTC.")
            self.expected_timezone = pytz.utc
        self.connection_lost = False
        self.is_connected_event = threading.Event()
        self.next_valid_req_id_ready = threading.Event()

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.req_id_counter = orderId
        print(f"NextValidId: {orderId}. Connection confirmed. Ready for requests.")
        self.is_connected_event.set()
        self.next_valid_req_id_ready.set()
        self.connection_lost = False

    def get_next_req_id(self):
        self.next_valid_req_id_ready.wait(timeout=10)
        if not self.next_valid_req_id_ready.is_set():
            print("Error: nextValidId not received. Cannot generate request ID.")
            return -1
        current_id = self.req_id_counter
        self.req_id_counter += 1
        return current_id

    def historicalData(self, reqId: int, bar: BarData):
        t_str_original = bar.date
        t_parts = bar.date.split(' ')
        processed_datetime_utc = None

        if len(t_parts) > 1 and ':' in t_parts[1]:
            datetime_str_component = t_parts[0] + ' ' + t_parts[1]
            try:
                naive_datetime = datetime.datetime.strptime(datetime_str_component, '%Y%m%d %H:%M:%S')
                if len(t_parts) == 3:
                    ibkr_timezone_str = t_parts[2]
                    try:
                        ibkr_timezone = pytz.timezone(ibkr_timezone_str)
                        localized_datetime = ibkr_timezone.localize(naive_datetime)
                        processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
                    except pytz.exceptions.UnknownTimeZoneError:
                        print(f"Warning (ReqId {reqId}): IBKR provided unknown timezone '{ibkr_timezone_str}' for '{t_str_original}'. Using expected_timezone '{self.expected_timezone}'.")
                        localized_datetime = self.expected_timezone.localize(naive_datetime)
                        processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
                else:
                    localized_datetime = self.expected_timezone.localize(naive_datetime)
                    processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
            except ValueError as e:
                print(f"Error (ReqId {reqId}): Parsing datetime string '{datetime_str_component}' from '{t_str_original}': {e}. Skipping bar.")
                return
        else:
            try:
                naive_datetime = datetime.datetime.strptime(t_parts[0], '%Y%m%d')
                localized_datetime = self.expected_timezone.localize(naive_datetime)
                processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
            except ValueError as e:
                print(f"Error (ReqId {reqId}): Parsing date string '{t_parts[0]}' from '{t_str_original}': {e}. Skipping bar.")
                return

        if processed_datetime_utc is None:
            print(f"Warning (ReqId {reqId}): Could not process timestamp: {t_str_original}. Skipping bar.")
            return

        if reqId in bar_collection:
            data = {
                'date': processed_datetime_utc,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': int(bar.volume) if bar.volume is not None and str(bar.volume).lower() != 'nan' and bar.volume != -1 else 0
            }
            bar_collection[reqId]['data'].append(data)
        else:
            print(f"Warning: Received historicalData for unknown reqId: {reqId}. Bar ignored: {bar}")

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        if reqId not in bar_collection:
            print(f"Warning: historicalDataEnd received for unknown reqId: {reqId}. No action taken.")
            return

        req_info = bar_collection[reqId]
        if not req_info['data']:
            print(f"No data received for ReqId {reqId} (Target End: {req_info['end_date_obj_for_req'].strftime('%Y-%m-%d')}, API Start: {start}, API End: {end}). Marking as complete (empty).")
            req_info['status'] = "complete"
            return

        df = pd.DataFrame(req_info['data'])

        if df.empty:
            print(f"DataFrame empty after creation for ReqId {reqId}. Marking as complete.")
            req_info['status'] = "complete"
            return

        if 'date' not in df.columns:
            print(f"Error (ReqId {reqId}): 'date' column missing in DataFrame. Marking as failed_permanent.")
            req_info['status'] = "failed_permanent"
            req_info['last_error_code'] = "INTERNAL_DATA_FORMAT_ERROR"
            return

        df['date'] = pd.to_datetime(df['date'], utc=True)
        df = df.sort_values('date')
        df.drop_duplicates(subset=['date'], keep='first', inplace=True)
        df['date'] = df['date'].dt.strftime('%Y-%m-%d %H:%M:%S')

        file_exists = os.path.exists(self.filename)
        write_header = not file_exists or os.path.getsize(self.filename) == 0

        try:
            df.to_csv(self.filename, mode='a', header=write_header, index=False)
            target_end_date_str = req_info['end_date_obj_for_req'].strftime('%Y-%m-%d')
            print(f"Data for ReqId {reqId} (target end {target_end_date_str}) successfully written to {self.filename}")
        except Exception as e:
            print(f"Error (ReqId {reqId}): Writing to CSV '{self.filename}': {e}. Marking for retry.")
            req_info['status'] = "error_retry"
            req_info['last_error_code'] = CSV_WRITE_ERROR_CODE
            if 'retry_count' not in req_info: req_info['retry_count'] = 0
            return

        req_info['data'] = []
        req_info['status'] = "complete"

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = ""):
        adv_reject_str = f" AdvancedOrderReject: {advancedOrderRejectJson}" if advancedOrderRejectJson else ""
        log_msg = f"Error. ReqId: {reqId}, Code: {errorCode}, Msg: {errorString}{adv_reject_str}"

        if errorCode in INFORMATIONAL_API_CODES:
            print(f"Informational/Warning (Code: {errorCode}, ReqId: {reqId}): {errorString}")
            if errorCode == 10314 and reqId != -1 and reqId in bar_collection:
                 print(f"Error 10314 for ReqId {reqId} (End Date/Time invalid). Marking as failed_permanent despite UTC format attempt.")
                 bar_collection[reqId]['status'] = "failed_permanent"
                 bar_collection[reqId]['last_error_code'] = errorCode
            return

        print(log_msg)

        if errorCode in RETRYABLE_ERROR_CODES:
            if not self.connection_lost:
                print(f"Connection error detected (Code: {errorCode}). Setting connection_lost = True.")
                self.connection_lost = True
                self.is_connected_event.clear()
                self.next_valid_req_id_ready.clear()
            else:
                print(f"Connection error (Code: {errorCode}) received while already in connection_lost state.")
            return

        if reqId != -1 and reqId in bar_collection:
            req_info = bar_collection[reqId]
            if req_info['status'] in ["complete", "failed_permanent"]:
                print(f"Error {errorCode} for already settled ReqId {reqId} ({req_info['status']}). Ignoring for status change.")
                return

            if errorCode in REQUEST_SPECIFIC_RETRYABLE_ERROR_CODES:
                print(f"Request-specific retryable error for ReqId {reqId} (Code: {errorCode}). Marking for retry.")
                req_info['status'] = "error_retry"
            elif errorCode == 200:
                 print(f"Error 200 for ReqId {reqId}: No security definition. Marking as failed_permanent.")
                 req_info['status'] = "failed_permanent"
            # Error 10299: Expected what to show is AGGTRADES...
            elif errorCode == 10299:
                 print(f"Error 10299 for ReqId {reqId}: Incorrect 'whatToShow'. Expected AGGTRADES. Marking as failed_permanent (config change needed).")
                 req_info['status'] = "failed_permanent" # Needs config change, not just retry
            elif errorCode == 162 and "Historical Market Data Service error message:No market data permissions" in errorString:
                print(f"Error 162 (No market data permissions) for ReqId {reqId}. Marking as failed_permanent.")
                req_info['status'] = "failed_permanent"
            else:
                print(f"Unhandled error for active ReqId {reqId} (Code: {errorCode}). Marking as failed_permanent.")
                req_info['status'] = "failed_permanent"

            req_info['last_error_code'] = errorCode
            if 'retry_count' not in req_info and req_info['status'] == "error_retry":
                req_info['retry_count'] = 0
            return

        if reqId == -1:
            print(f"General system message (Code: {errorCode}). Not a connection error, informational, or request-specific.")

def load_config_from_path(config_path="datadownload_config.json"):
    try:
        with open(config_path, 'r') as f:
            config_data = json.load(f)
        print(f"Configuration loaded from {config_path}")
        return config_data
    except FileNotFoundError:
        print(f"Error: Configuration file '{config_path}' not found. Please create it. Exiting.")
        exit(1)
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from '{config_path}': {e}. Exiting.")
        exit(1)
    except Exception as e:
        print(f"An unexpected error occurred while loading '{config_path}': {e}. Exiting.")
        exit(1)

# MODIFIED: Added what_to_show_param
def request_historical_data(app: IBapi, contract_obj: Contract, end_date_time_api_str: str, req_id: int, use_rth_param: int, what_to_show_param: str):
    print(f"Requesting historical data for ReqId: {req_id}, Contract: {contract_obj.symbol}, API EndDateTime (UTC): '{end_date_time_api_str}', UseRTH: {use_rth_param}, WhatToShow: {what_to_show_param}")
    app.reqHistoricalData(reqId=req_id,
                          contract=contract_obj,
                          endDateTime=end_date_time_api_str,
                          durationStr="1 W",
                          barSizeSetting="1 min",
                          whatToShow=what_to_show_param, # MODIFIED: Use passed parameter
                          useRTH=use_rth_param,
                          formatDate=1,
                          keepUpToDate=False,
                          chartOptions=[])

def main():
    config = load_config_from_path()

    contract_cfg = config.get("contract_details", {})
    instrument_tz_str = config.get("instrument_timezone", "America/New_York")

    contract = Contract()
    contract.symbol = contract_cfg.get("symbol")
    contract.secType = contract_cfg.get("secType")
    contract.exchange = contract_cfg.get("exchange")
    contract.currency = contract_cfg.get("currency")

    if not all([contract.symbol, contract.secType, contract.exchange, contract.currency]):
        print("Error: Essential contract details (symbol, secType, exchange, currency) missing in config.json. Exiting.")
        return
    print(f"Contract to download: {contract.symbol} ({contract.secType}) on {contract.exchange} in {contract.currency}")
    print(f"Using instrument timezone for date processing: {instrument_tz_str}")

    date_range_cfg = config.get("date_range", {})
    api_cfg = config.get("api_settings", {})
    output_cfg = config.get("output", {})

    IBKR_PORT = api_cfg.get("ibkr_port", 7497)
    CLIENT_ID = api_cfg.get("client_id", 1)
    MAX_ACTIVE_REQUESTS = api_cfg.get("max_active_requests", 5)
    INTER_REQUEST_DELAY = api_cfg.get("inter_request_delay_seconds", 3)
    MAX_CONNECT_RETRIES = api_cfg.get("max_connect_retries", 5)
    CONNECT_RETRY_DELAY = api_cfg.get("connect_retry_delay_seconds", 30)
    MAX_REQUEST_RETRIES = api_cfg.get("max_request_retries", 3)
    REQUEST_RETRY_DELAY = api_cfg.get("request_retry_delay_seconds", 20)

    USE_RTH_CONFIG = api_cfg.get("use_rth", 1)
    if USE_RTH_CONFIG not in [0, 1]:
        print(f"Warning: Invalid 'use_rth' value '{USE_RTH_CONFIG}' in config. Defaulting to 1 (RTH data).")
        USE_RTH_CONFIG = 1
    print(f"Historical data will be requested with useRTH = {USE_RTH_CONFIG} (0=all hours, 1=regular trading hours only)")

    # MODIFIED: Read what_to_show from config, default to "TRADES"
    WHAT_TO_SHOW_CONFIG = api_cfg.get("what_to_show", "TRADES").upper()
    valid_what_to_show = ["TRADES", "MIDPOINT", "BID", "ASK", "BID_ASK", "AGGTRADES", "HISTORICAL_VOLATILITY", "OPTION_IMPLIED_VOLATILITY", "YIELD_BID", "YIELD_ASK", "YIELD_LAST", "YIELD_BID_ASK"]
    if WHAT_TO_SHOW_CONFIG not in valid_what_to_show:
        print(f"Warning: Invalid 'what_to_show' value '{WHAT_TO_SHOW_CONFIG}' in config. Defaulting to TRADES.")
        WHAT_TO_SHOW_CONFIG = "TRADES"
    print(f"Historical data will be requested with whatToShow = {WHAT_TO_SHOW_CONFIG}")


    today = datetime.date.today()
    default_start_year = today.year - 1 if today.month == 1 and today.day == 1 else today.year

    overall_start_date = datetime.date(
        date_range_cfg.get("start_year", default_start_year),
        date_range_cfg.get("start_month", 1),
        date_range_cfg.get("start_day", 1)
    )
    overall_end_date = datetime.date(
        date_range_cfg.get("end_year", today.year),
        date_range_cfg.get("end_month", today.month),
        date_range_cfg.get("end_day", today.day)
    )
    print(f"Overall date range for download: {overall_start_date.strftime('%Y-%m-%d')} to {overall_end_date.strftime('%Y-%m-%d')}")


    filename_template = output_cfg.get("filename_template", "{symbol}_1min_{start_year}-{end_year}_utc.csv")
    filename = filename_template.format(
        symbol=contract.symbol.replace("/", "_"),
        start_year=overall_start_date.year,
        end_year=overall_end_date.year
    )
    print(f"Data will be saved to: {filename} (timestamps in UTC)")

    current_run_start_date = overall_start_date
    if os.path.exists(filename):
        try:
            if os.path.getsize(filename) > 0:
                df_all_dates = pd.read_csv(filename, usecols=['date'])
                if not df_all_dates.empty and 'date' in df_all_dates.columns:
                    df_all_dates['date'] = pd.to_datetime(df_all_dates['date'], errors='coerce', utc=True)
                    df_all_dates.dropna(subset=['date'], inplace=True)
                    if not df_all_dates.empty:
                        last_recorded_datetime_utc = df_all_dates['date'].max()
                        if pd.notna(last_recorded_datetime_utc):
                            resume_from_date = last_recorded_datetime_utc.date() + datetime.timedelta(days=1)
                            if resume_from_date > current_run_start_date:
                                print(f"Resuming download. Last data in {filename} up to {last_recorded_datetime_utc.strftime('%Y-%m-%d %H:%M:%S %Z')}.")
                                current_run_start_date = resume_from_date
                                print(f"New effective start date for this run: {current_run_start_date}")
                    else: print(f"No valid dates found in existing {filename} after parsing. Using original start_date.")
            else: print(f"{filename} exists but is empty. Using original start_date: {current_run_start_date}")
        except pd.errors.EmptyDataError: print(f"Warning: {filename} is empty. Using original start_date: {current_run_start_date}")
        except KeyError: print(f"Warning: 'date' column not found in {filename}. Cannot resume. Using original start_date.")
        except Exception as e: print(f"Warning: Error reading existing {filename} for resume: {e}. Using original start_date.")

    if current_run_start_date > overall_end_date:
        print(f"Effective start date {current_run_start_date} is after overall end date {overall_end_date}. No new data to download.")
        return

    app = IBapi(filename=filename, expected_timezone_str=instrument_tz_str)
    api_thread = None

    connected_successfully = False
    for attempt in range(MAX_CONNECT_RETRIES):
        print(f"Attempting to connect to IBKR (Port: {IBKR_PORT}, ClientID: {CLIENT_ID}) (Attempt {attempt + 1}/{MAX_CONNECT_RETRIES})...")
        app.is_connected_event.clear(); app.next_valid_req_id_ready.clear(); app.connection_lost = False
        if app.isConnected(): app.disconnect(); time.sleep(0.3)
        if api_thread and api_thread.is_alive(): api_thread.join(timeout=5)

        app.connect('127.0.0.1', IBKR_PORT, clientId=CLIENT_ID)
        api_thread = threading.Thread(target=app.run, daemon=True, name="IBAPIThread")
        api_thread.start()

        if app.is_connected_event.wait(timeout=20) and app.next_valid_req_id_ready.wait(timeout=15):
            print("Successfully connected and nextValidId received.")
            connected_successfully = True; break
        else:
            print("Failed to connect or receive nextValidId within timeout.")
            if app.isConnected(): app.disconnect()
            if api_thread and api_thread.is_alive(): api_thread.join(timeout=5)
            if attempt < MAX_CONNECT_RETRIES - 1: print(f"Retrying connection in {CONNECT_RETRY_DELAY} seconds..."); time.sleep(CONNECT_RETRY_DELAY)
            else: print("Max connection retries reached. Exiting."); return
    if not connected_successfully: return

    tasks_to_process = []
    temp_date_iterator = overall_end_date
    while temp_date_iterator >= current_run_start_date:
        tasks_to_process.append(temp_date_iterator)
        temp_date_iterator -= datetime.timedelta(days=7) # 1 week duration per request
    tasks_to_process.reverse()
    if not tasks_to_process and current_run_start_date <= overall_end_date: # Ensure at least one task if range is valid
        tasks_to_process.append(overall_end_date)
    print(f"Generated {len(tasks_to_process)} weekly tasks from {current_run_start_date} to {overall_end_date}.")

    try:
        while True:
            if app.connection_lost:
                print("Connection lost. Attempting to reconnect...")
                if app.isConnected(): app.disconnect(); time.sleep(0.3)
                if api_thread and api_thread.is_alive(): api_thread.join(timeout=10)
                reconnected = False
                for rec_attempt in range(MAX_CONNECT_RETRIES):
                    print(f"Reconnect attempt {rec_attempt + 1}/{MAX_CONNECT_RETRIES}...")
                    app.is_connected_event.clear(); app.next_valid_req_id_ready.clear(); app.connection_lost = False
                    app.connect('127.0.0.1', IBKR_PORT, clientId=CLIENT_ID)
                    api_thread = threading.Thread(target=app.run, daemon=True, name="IBAPIThread-Reconnect")
                    api_thread.start()
                    if app.is_connected_event.wait(timeout=20) and app.next_valid_req_id_ready.wait(timeout=15):
                        print("Successfully reconnected."); reconnected = True; break
                    else:
                        print("Reconnect attempt failed.")
                        if app.isConnected(): app.disconnect()
                        if api_thread and api_thread.is_alive(): api_thread.join(timeout=10)
                        if rec_attempt < MAX_CONNECT_RETRIES - 1: time.sleep(CONNECT_RETRY_DELAY)
                if not reconnected: print("Failed to reconnect after multiple attempts. Exiting processing loop."); break

            active_requests_count = sum(1 for req_info in bar_collection.values() if req_info.get('status') == "incomplete")

            for r_id in list(bar_collection.keys()): # Iterate over copy of keys for safe removal/modification
                req_info = bar_collection.get(r_id)
                if req_info and req_info['status'] == "error_retry":
                    if active_requests_count < MAX_ACTIVE_REQUESTS:
                        if req_info.get('retry_count', 0) < MAX_REQUEST_RETRIES:
                            req_info['retry_count'] = req_info.get('retry_count', 0) + 1
                            error_code = req_info.get('last_error_code', 'N/A')
                            original_end_date_obj = req_info['end_date_obj_for_req']

                            print(f"Retrying request {r_id} (Attempt {req_info['retry_count']}/{MAX_REQUEST_RETRIES}), Target EndDate: {original_end_date_obj.strftime('%Y-%m-%d')}, LastError: {error_code}")
                            req_info['status'] = "incomplete"

                            naive_dt_local_retry = datetime.datetime.combine(original_end_date_obj, datetime.time(23, 59, 59))
                            localized_dt_local_retry = app.expected_timezone.localize(naive_dt_local_retry)
                            dt_utc_retry = localized_dt_local_retry.astimezone(pytz.utc)
                            # Using space and " UTC" for clarity with API
                            end_date_str_for_api_retry_utc = dt_utc_retry.strftime("%Y%m%d %H:%M:%S UTC")

                            req_info['data'] = []
                            # MODIFIED: Pass USE_RTH_CONFIG and WHAT_TO_SHOW_CONFIG
                            request_historical_data(app, contract, end_date_str_for_api_retry_utc, r_id, USE_RTH_CONFIG, WHAT_TO_SHOW_CONFIG)
                            active_requests_count += 1
                            time.sleep(REQUEST_RETRY_DELAY) # Use specific retry delay
                        else:
                            print(f"Max retries for ReqId {r_id} reached. Marking as failed_permanent.")
                            req_info['status'] = "failed_permanent"
                    else: break # Max active requests reached, stop processing retries for now

            active_requests_count = sum(1 for req_info in bar_collection.values() if req_info.get('status') == "incomplete")

            while tasks_to_process and active_requests_count < MAX_ACTIVE_REQUESTS:
                date_to_fetch_end = tasks_to_process.pop(0)
                current_req_id = app.get_next_req_id()
                if current_req_id == -1:
                    print("Failed to get next request ID. Will retry connection/setup.")
                    app.connection_lost = True
                    tasks_to_process.insert(0, date_to_fetch_end) # Put task back
                    break # Break from this inner while to re-check connection

                naive_dt_local_new = datetime.datetime.combine(date_to_fetch_end, datetime.time(23, 59, 59))
                localized_dt_local_new = app.expected_timezone.localize(naive_dt_local_new)
                dt_utc_new = localized_dt_local_new.astimezone(pytz.utc)
                # Using space and " UTC" for clarity with API
                end_date_str_req_for_api_utc = dt_utc_new.strftime("%Y%m%d %H:%M:%S UTC")

                print(f"Preparing new request for target end date: {date_to_fetch_end.strftime('%Y-%m-%d')} (API str: {end_date_str_req_for_api_utc}), ReqId: {current_req_id}")
                bar_collection[current_req_id] = {
                    'data': [], 'status': "incomplete",
                    'end_date_obj_for_req': date_to_fetch_end,
                    'retry_count': 0, 'last_error_code': None
                }
                # MODIFIED: Pass USE_RTH_CONFIG and WHAT_TO_SHOW_CONFIG
                request_historical_data(app, contract, end_date_str_req_for_api_utc, current_req_id, USE_RTH_CONFIG, WHAT_TO_SHOW_CONFIG)
                active_requests_count += 1
                time.sleep(INTER_REQUEST_DELAY)

            all_tasks_initiated = not tasks_to_process
            all_requests_settled = True
            pending_requests_exist = False
            if not bar_collection and all_tasks_initiated: break # No requests made and no tasks left
            for req_status_info in bar_collection.values():
                status = req_status_info.get('status')
                if status == "incomplete" or status == "error_retry":
                    all_requests_settled = False; pending_requests_exist = True; break
            if all_tasks_initiated and all_requests_settled: break
            if not pending_requests_exist and not tasks_to_process: break # No pending and no new tasks
            time.sleep(1)

    except KeyboardInterrupt:
        print("KeyboardInterrupt received. Shutting down...")
    finally:
        if bar_collection:
            completed_count = sum(1 for r_info in bar_collection.values() if r_info.get('status') == "complete")
            failed_count = sum(1 for r_info in bar_collection.values() if r_info.get('status') == "failed_permanent")
            in_progress_count = sum(1 for r_info in bar_collection.values() if r_info.get('status') in ["incomplete", "error_retry"])
            print(f"\nProcessing summary. Total requests initiated: {len(bar_collection)}.")
            print(f"  Successfully completed and written: {completed_count}")
            print(f"  Permanently failed: {failed_count}")
            print(f"  Still in progress/pending retry at exit: {in_progress_count}")
            if failed_count > 0 or in_progress_count > 0:
                print("  Details for non-completed requests (target end dates):")
                for r_id, info in bar_collection.items():
                    if info.get('status') != 'complete':
                        err_code = info.get('last_error_code', 'N/A')
                        print(f"    ReqId: {r_id}, Status: {info.get('status')}, Target EndDate: {info['end_date_obj_for_req'].strftime('%Y-%m-%d')}, Error: {err_code}")
        else: print("\nNo data requests were processed.")

        print(f"\nFinal data available in {filename}")
        if app.isConnected(): print("Disconnecting from IBKR..."); app.disconnect()
        if api_thread and api_thread.is_alive():
            print("Waiting for API thread to terminate...")
            api_thread.join(timeout=10)
            if api_thread.is_alive(): print("Warning: API thread did not terminate gracefully.")
        print("Script finished.")

if __name__ == "__main__":
    main()
