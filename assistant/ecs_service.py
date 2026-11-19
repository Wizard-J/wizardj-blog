"""Entry point for the Blog assistant on ECS."""
from server import serve


if __name__ == "__main__":
    serve(port=18766)
