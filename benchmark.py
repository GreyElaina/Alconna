import time
from arclet.alconna import Alconna, Args, AnyOne, command_manager, namespace
import cProfile
import pstats


class Plain:
    type = "Plain"
    text: str

    def __init__(self, t: str):
        self.text = t


class At:
    type = "At"
    target: int

    def __init__(self, t: int):
        self.target = t


with namespace("test") as np:
    np.enable_message_cache = False
    np.to_text = lambda x: x.text if isinstance(x, Plain) else None
    alc = Alconna(
        ["."],
        "test",
        Args["bar", AnyOne]
    )

argv = command_manager.resolve(alc)
analyser = command_manager.require(alc)
print(alc)
msg = [Plain(".test"), At(124)]
count = 20000

if __name__ == "__main__":

    sec = 0.0
    for _ in range(count):
        st = time.perf_counter()
        argv.build(msg)
        analyser.process(argv)
        sec += time.perf_counter() - st
    print(f"Alconna: {count / sec:.2f}msg/s")

    print("RUN 2:")
    li = 0.0

    for _ in range(count):
        st = time.thread_time_ns()
        argv.build(msg)
        analyser.process(argv)
        li += (time.thread_time_ns() - st)

    print(f"Alconna: {li / count} ns per loop with {count} loops")

    command_manager.records.clear()

    prof = cProfile.Profile()
    prof.enable()
    for _ in range(count):
        argv.build(msg)
        analyser.process(argv)
    prof.create_stats()

    stats = pstats.Stats(prof)
    stats.strip_dirs()
    stats.sort_stats('tottime')
    stats.print_stats(20)
