import logging.config


def configure_logging() -> None:
    """Configure the "app" logger only; uvicorn configures its own loggers.

    The root logger is left alone, and records still propagate to it, so
    tools that listen on the root logger (e.g. pytest's caplog) see them.
    """
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "default"},
            },
            "loggers": {
                "app": {"handlers": ["console"], "level": "INFO"},
            },
        }
    )
