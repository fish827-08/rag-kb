"""空闲卸载守护线程（v2.1 资源占用优化）。

独立模块以便 create_app（服务生命周期）与 CLI 复用；create_app 返回的是
经中间件包装的 ASGI 函数，无法再从外部访问 app.state，因此守护线程必须在
工厂内部、拿得到 KBService 的地方启动。
"""
import logging
import threading


def start_idle_watchdog(embedder, idle_seconds: int,
                        check_seconds: float | None = None):
    """启动空闲卸载守护线程（daemon），返回停止事件；idle_seconds<=0 不启动。

    embedder 可以是 Embedder 实例，也可以是返回实例的可调用对象（延迟解析，
    兼容测试替身与延迟初始化）。每轮调用 unload_if_idle()；异常记 WARNING，
    守护线程不因单次失败退出。
    """
    stop = threading.Event()
    if idle_seconds <= 0:
        return stop
    interval = (check_seconds if check_seconds is not None
                else max(1.0, min(30.0, idle_seconds / 4)))

    def _loop() -> None:
        log = logging.getLogger("kb.idle")
        while not stop.wait(interval):
            try:
                target = embedder() if callable(embedder) else embedder
                if target.unload_if_idle():
                    log.info("嵌入模型空闲超过 %s 秒，已卸载", idle_seconds)
            except Exception as exc:  # noqa: BLE001 - 守护线程不因单次失败退出
                log.warning("空闲卸载检查失败：%s", exc)

    threading.Thread(target=_loop, name="kb-idle-unload", daemon=True).start()
    return stop
