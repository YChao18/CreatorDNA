"""Local credentials stay out of provider settings and task artifacts."""

from dotenv import find_dotenv, load_dotenv


def load_environment():
    """Use the nearest project .env; existing process variables take precedence."""
    path = find_dotenv(usecwd=True)
    if path:
        load_dotenv(path, override=False, encoding="utf-8-sig")
