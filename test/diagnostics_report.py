"""Browser fixture: two outputs report the same computed warning."""
import eval_live

reg = eval_live.Registry()


def report_timeouts(data):
    if any(row["status"] == "timed-out" for row in data["measurements"]):
        eval_live.warn("Some treatments timed out")


@reg.table("Counts")
def counts(data):
    report_timeouts(data)
    return [{"requested": len(data["measurements"])}]


@reg.graph("Values")
def values(data):
    import matplotlib.pyplot as plt
    report_timeouts(data)
    fig, ax = plt.subplots()
    ax.plot([row["value"] for row in data["measurements"] if row["status"] == "success"])
    return fig
