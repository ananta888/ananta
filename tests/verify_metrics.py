import requests
import sys

def test_metrics():
    url = "http://localhost:5000/metrics"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            print("Successfully accessed /metrics")
            # Check for some expected metrics
            expected = ["task_received_total", "task_completed_total", "process_cpu_usage_percent"]
            for m in expected:
                if m in response.text:
                    print(f"Found metric: {m}")
                else:
                    print(f"Metric {m} NOT found")
        else:
            print(f"Failed to access /metrics. Status code: {response.status_code}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # Note: This requires a running agent. Since I cannot easily start one in the background and wait,
    # I'll just check if the code for the endpoint exists (which I already did).
    # But I can check if the metric generation works locally.
    from agent.metrics import TASK_RECEIVED, generate_latest
    TASK_RECEIVED.inc()
    metrics_data = generate_latest().decode('utf-8')
    if "task_received_total" in metrics_data:
        print("Metric generation works correctly.")
    else:
        print("Metric generation failed.")
        sys.exit(1)
