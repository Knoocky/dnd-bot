import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(project_root: Path) -> logging.Logger:
    log_dir = project_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_level_name = os.getenv("BOT_LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING)

    if not any(getattr(handler, "_dnd_bot_logging", False) for handler in root_logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        console_handler._dnd_bot_logging = True
        root_logger.addHandler(console_handler)

        file_handler = RotatingFileHandler(
            log_dir / "bot.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        file_handler._dnd_bot_logging = True
        root_logger.addHandler(file_handler)
    else:
        for handler in root_logger.handlers:
            if getattr(handler, "_dnd_bot_logging", False):
                handler.setLevel(log_level)

    logging.captureWarnings(True)

    logger = logging.getLogger("dnd_bot")
    logger.setLevel(log_level)

    noisy_loggers = [
        "discord",
        "discord.client",
        "discord.gateway",
        "discord.http",
        "openai",
        "openai._base_client",
        "httpx",
        "httpcore",
        "anthropic",
        "asyncio",
    ]
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    logger.info(
        "Logging configured. level=%s, file=%s",
        logging.getLevelName(log_level),
        log_dir / "bot.log",
    )
    return logger


def enable_project_function_logging(project_root: Path):
    logger = logging.getLogger("dnd_bot")
    logger.warning(
        "Per-function profiler logging is disabled because it breaks SDK response parsing and blocks the event loop."
    )
    return logger
