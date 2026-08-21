"""兼容入口：系统配置已经并入正式主界面。"""

from system.gui.live_dashboard import main


if __name__ == "__main__":
    raise SystemExit(main())
