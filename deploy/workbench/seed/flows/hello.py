from prefect import flow, get_run_logger


@flow(name="hello", log_prints=True)
def hello(name: str = "Prefect"):
    message = f"Hello, {name}! Edit /workspace/flows/hello.py in your browser."
    get_run_logger().info(message)
    return message
