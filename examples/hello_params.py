"""Print a greeting using task parameters."""


def main(params):
    name = params.get("name", "world")
    print(f"Hello, {name}!")
