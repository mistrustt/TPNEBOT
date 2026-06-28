"""Chart rendering utilities for metrics commands.

This module provides reusable chart rendering functions for Discord bot metrics
with improved styling and visualization options.
"""

import io
from datetime import datetime
from typing import Literal, Optional

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.axes import Axes
import discord


# Color palette for consistent styling
COLORS = {
    "primary": "#5865F2",      # Discord blurple
    "success": "#57F287",      # Discord green
    "warning": "#FEE75C",      # Discord yellow
    "danger": "#ED4245",       # Discord red
    "info": "#00D4FF",         # Cyan
    "purple": "#9B59B6",       # Purple
    "orange": "#E67E22",       # Orange
    "pink": "#FF6B9D",         # Pink
    "teal": "#1ABC9C",         # Teal
}

COLOR_LIST = list(COLORS.values())

# Chart style configuration
CHART_STYLE = {
    "figure.facecolor": "#2F3136",      # Discord dark background
    "axes.facecolor": "#36393F",         # Slightly lighter
    "axes.edgecolor": "#72767D",         # Discord gray
    "axes.labelcolor": "#FFFFFF",         # White labels
    "text.color": "#FFFFFF",              # White text
    "xtick.color": "#B9BBBE",            # Light gray ticks
    "ytick.color": "#B9BBBE",
    "grid.color": "#40444B",              # Subtle grid
    "grid.alpha": 0.3,
}


def setup_chart_style() -> None:
    """Apply Discord-themed styling to matplotlib."""
    plt.style.use("dark_background")
    for key, value in CHART_STYLE.items():
        plt.rcParams[key] = value


def create_figure(figsize: tuple[float, float] = (10, 6)) -> tuple[Figure, Axes]:
    """Create a styled figure and axes.

    Args:
        figsize: Figure size as (width, height) tuple.

    Returns:
        Tuple of (Figure, Axes) objects.
    """
    setup_chart_style()
    fig, ax = plt.subplots(figsize=figsize)
    fig.patch.set_facecolor(CHART_STYLE["figure.facecolor"])
    ax.set_facecolor(CHART_STYLE["axes.facecolor"])
    return fig, ax


def _format_number(value: int | float) -> str:
    """Human readable number label for chart annotations."""
    value = float(value)
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:.1f}"


def _trim_labels(labels: list[str], max_len: int = 18) -> list[str]:
    """Trim labels to avoid overlapping on charts."""
    return [
        (label[: max_len - 1] + "…") if len(label) > max_len else label
        for label in labels
    ]


def format_dates_on_axis(ax: Axes, dates: list[datetime], rotation: int = 45) -> None:
    """Format date axis with proper date formatting.

    Args:
        ax: Matplotlib axes object.
        dates: List of datetime objects.
        rotation: Rotation angle for date labels.
    """
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=rotation, ha="right")


def render_line_chart(
    labels: list[str],
    values: list[int | float],
    title: str,
    ylabel: str,
    color: str = COLORS["primary"],
    fill: bool = True,
    marker: str = "o",
    markersize: int = 4,
) -> io.BytesIO:
    """Render a line chart with improved styling.

    Args:
        labels: X-axis labels (dates or categories).
        values: Y-axis values.
        title: Chart title.
        ylabel: Y-axis label.
        color: Line color.
        fill: Whether to fill area under the line.
        marker: Marker style.
        markersize: Marker size.

    Returns:
        BytesIO buffer containing the PNG image.
    """
    fig, ax = create_figure(figsize=(10, 5))

    x_values = range(len(labels))

    ax.plot(
        x_values,
        values,
        color=color,
        marker=marker,
        markersize=markersize,
        linewidth=2.5,
        label=title,
    )

    if fill:
        ax.fill_between(x_values, values, alpha=0.25, color=color)

    ax.set_xticks(x_values)
    # Avoid label crowding: show every nth label
    step = max(1, len(labels) // 10)
    ax.set_xticklabels(
        [label if i % step == 0 else "" for i, label in enumerate(labels)],
        rotation=45,
        ha="right",
    )

    ax.set_xlabel("Date", color=CHART_STYLE["axes.labelcolor"])
    ax.set_ylabel(ylabel, color=CHART_STYLE["axes.labelcolor"])
    ax.set_title(title, fontsize=14, fontweight="bold", color=CHART_STYLE["text.color"], pad=10)

    ax.grid(True, alpha=CHART_STYLE["grid.alpha"], color=CHART_STYLE["grid.color"])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: _format_number(x)))

    # Annotate max, min, and last points
    if values:
        for idx in _interesting_indices(values):
            ax.annotate(
                _format_number(values[idx]),
                xy=(x_values[idx], values[idx]),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                color=CHART_STYLE["text.color"],
                bbox=dict(boxstyle="round,pad=0.2", facecolor="black", alpha=0.5),
            )

    plt.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
    buffer.seek(0)
    plt.close(fig)

    return buffer


def _interesting_indices(values: list[int | float]) -> list[int]:
    """Return indices of min, max, and last value for annotation."""
    if not values:
        return []
    result = {len(values) - 1}
    result.add(min(range(len(values)), key=lambda i: values[i]))
    result.add(max(range(len(values)), key=lambda i: values[i]))
    return sorted(result)


def render_bar_chart(
    labels: list[str],
    values: list[int | float],
    title: str,
    ylabel: str,
    color: str = COLORS["primary"],
    horizontal: bool = True,
    show_values: bool = True,
    limit: Optional[int] = 15,
) -> io.BytesIO:
    """Render a bar chart with improved styling.

    Args:
        labels: Bar labels.
        values: Bar values.
        title: Chart title.
        ylabel: Y-axis label.
        color: Bar color.
        horizontal: Whether to render horizontal bars (default True for readability).
        show_values: Whether to show value labels on bars.
        limit: Optional max number of bars to render (top N by value).

    Returns:
        BytesIO buffer containing the PNG image.
    """
    # Keep top N by value for readability; preserve original order within the slice.
    if limit and len(values) > limit:
        indexed = sorted(enumerate(values), key=lambda x: x[1], reverse=True)[:limit]
        kept_indices = [i for i, _ in indexed]
        labels = [labels[i] for i in kept_indices]
        values = [values[i] for i in kept_indices]

    labels = _trim_labels(labels, max_len=22)

    # Dynamic height to avoid squashed bars
    height = max(4, len(values) * 0.45 + 1.2)
    fig, ax = create_figure(figsize=(10, height))

    if horizontal:
        bars = ax.barh(range(len(labels)), values, color=color, edgecolor="white", linewidth=0.5)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels)
        ax.invert_yaxis()
        ax.set_xlabel(ylabel, color=CHART_STYLE["axes.labelcolor"])
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: _format_number(x)))

        if show_values:
            max_val = max(values) if values else 1
            for i, (bar, val) in enumerate(zip(bars, values)):
                ax.text(
                    val + max_val * 0.015,
                    i,
                    _format_number(val),
                    va="center",
                    fontsize=9,
                    color=CHART_STYLE["text.color"],
                )
    else:
        bars = ax.bar(range(len(labels)), values, color=color, edgecolor="white", linewidth=0.5)
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel(ylabel, color=CHART_STYLE["axes.labelcolor"])
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: _format_number(x)))

        if show_values:
            max_val = max(values) if values else 1
            for bar, val in zip(bars, values):
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    val + max_val * 0.015,
                    _format_number(val),
                    ha="center",
                    va="bottom",
                    fontsize=9,
                    color=CHART_STYLE["text.color"],
                )

    ax.set_title(title, fontsize=14, fontweight="bold", color=CHART_STYLE["text.color"], pad=10)
    ax.grid(True, alpha=CHART_STYLE["grid.alpha"], color=CHART_STYLE["grid.color"], axis="x" if horizontal else "y")

    plt.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
    buffer.seek(0)
    plt.close(fig)

    return buffer


def render_multi_line_chart(
    labels: list[str],
    datasets: dict[str, list[int | float]],
    title: str,
    ylabel: str,
    colors: Optional[list[str]] = None,
) -> io.BytesIO:
    """Render a multi-line chart for comparing multiple metrics.

    Args:
        labels: X-axis labels.
        datasets: Dictionary mapping series names to their values.
        title: Chart title.
        ylabel: Y-axis label.
        colors: Optional list of colors for each series.

    Returns:
        BytesIO buffer containing the PNG image.
    """
    fig, ax = create_figure(figsize=(10, 5))

    if colors is None:
        color_list = COLOR_LIST[:len(datasets)]
    else:
        color_list = colors

    x_values = range(len(labels))

    for (name, values), color in zip(datasets.items(), color_list):
        ax.plot(
            x_values,
            values,
            color=color,
            marker="o",
            markersize=3,
            linewidth=2,
            label=name,
        )

    ax.set_xticks(x_values)
    step = max(1, len(labels) // 10)
    ax.set_xticklabels(
        [label if i % step == 0 else "" for i, label in enumerate(labels)],
        rotation=45,
        ha="right",
    )
    ax.set_xlabel("Date", color=CHART_STYLE["axes.labelcolor"])
    ax.set_ylabel(ylabel, color=CHART_STYLE["axes.labelcolor"])
    ax.set_title(title, fontsize=14, fontweight="bold", color=CHART_STYLE["text.color"], pad=10)
    ax.legend(loc="best", facecolor=CHART_STYLE["axes.facecolor"])
    ax.grid(True, alpha=CHART_STYLE["grid.alpha"], color=CHART_STYLE["grid.color"])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: _format_number(x)))

    plt.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
    buffer.seek(0)
    plt.close(fig)

    return buffer


def render_pie_chart(
    labels: list[str],
    values: list[int | float],
    title: str,
    show_percentages: bool = True,
) -> io.BytesIO:
    """Render a pie chart for distribution visualization.

    Args:
        labels: Slice labels.
        values: Slice values.
        title: Chart title.
        show_percentages: Whether to show percentage labels.

    Returns:
        BytesIO buffer containing the PNG image.
    """
    fig, ax = create_figure(figsize=(8, 8))

    colors = COLOR_LIST[:len(labels)]

    wedges, texts, autotexts = ax.pie(
        values,
        labels=labels,
        colors=colors,
        autopct="%1.1f%%" if show_percentages else None,
        startangle=90,
        textprops={"color": CHART_STYLE["text.color"]},
    )

    for autotext in autotexts:
        autotext.set_color("white")
        autotext.set_fontweight("bold")

    ax.set_title(title, fontsize=14, fontweight="bold", color=CHART_STYLE["text.color"], pad=10)

    plt.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
    buffer.seek(0)
    plt.close(fig)

    return buffer


def render_stacked_bar_chart(
    labels: list[str],
    datasets: dict[str, list[int | float]],
    title: str,
    ylabel: str,
    colors: Optional[list[str]] = None,
) -> io.BytesIO:
    """Render a stacked bar chart for cumulative metrics.

    Args:
        labels: Bar labels.
        datasets: Dictionary mapping series names to their values.
        title: Chart title.
        ylabel: Y-axis label.
        colors: Optional list of colors for each series.

    Returns:
        BytesIO buffer containing the PNG image.
    """
    fig, ax = create_figure(figsize=(10, 5))

    if colors is None:
        color_list = COLOR_LIST[:len(datasets)]
    else:
        color_list = colors

    x_values = range(len(labels))
    bottom = [0] * len(labels)

    for (name, values), color in zip(datasets.items(), color_list):
        ax.bar(x_values, values, bottom=bottom, label=name, color=color)
        bottom = [b + v for b, v in zip(bottom, values)]

    ax.set_xticks(x_values)
    step = max(1, len(labels) // 10)
    ax.set_xticklabels(
        [label if i % step == 0 else "" for i, label in enumerate(labels)],
        rotation=45,
        ha="right",
    )
    ax.set_xlabel("Date", color=CHART_STYLE["axes.labelcolor"])
    ax.set_ylabel(ylabel, color=CHART_STYLE["axes.labelcolor"])
    ax.set_title(title, fontsize=14, fontweight="bold", color=CHART_STYLE["text.color"], pad=10)
    ax.legend(loc="upper left", facecolor=CHART_STYLE["axes.facecolor"])
    ax.grid(True, alpha=CHART_STYLE["grid.alpha"], color=CHART_STYLE["grid.color"])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: _format_number(x)))

    plt.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
    buffer.seek(0)
    plt.close(fig)

    return buffer


class MetricsChartView(discord.ui.View):
    """Discord UI View for metrics charts with interactive buttons."""

    def __init__(
        self,
        bot,
        user_id: int,
        chart_type: Literal["line", "bar", "pie", "stacked", "multi"],
        title: str,
        labels: list[str],
        values: list[int | float],
        ylabel: str = "Value",
        datasets: Optional[dict[str, list[int | float]]] = None,
        timeout: float = 180.0,
        min_value: int | float = 0,
        limit: Optional[int] = 15,
    ):
        """Initialize the metrics chart view.

        Args:
            bot: Discord bot instance.
            user_id: User ID who requested the chart.
            chart_type: Type of chart to render.
            title: Chart title.
            labels: X-axis labels.
            values: Y-axis values (for single-series charts).
            ylabel: Y-axis label.
            datasets: Multi-series data (for multi-line or stacked charts).
            timeout: View timeout in seconds.
            min_value: Minimum value threshold. Values below this are hidden. Default 0.
            limit: Optional max number of data points to show on the chart.
        """
        super().__init__(timeout=timeout)
        self.bot = bot
        self.user_id = user_id
        self.chart_type = chart_type
        self.title = title
        self.labels = labels
        self.values = values
        self.ylabel = ylabel
        self.datasets = datasets
        self.min_value = min_value
        self.limit = limit

        # Filter out values below threshold for single-series charts
        if self.chart_type in ("line", "bar", "pie"):
            filtered_data = [
                (label, value) for label, value in zip(self.labels, self.values)
                if value >= self.min_value
            ]
            if filtered_data:
                self.labels, self.values = zip(*filtered_data)
                self.labels = list(self.labels)
                self.values = list(self.values)
            else:
                self.labels = labels
                self.values = values

        # Filter datasets for multi-series charts
        if self.chart_type in ("stacked", "multi") and self.datasets:
            if self.labels and self.datasets:
                first_series = list(self.datasets.values())[0]
                keep_indices = [
                    i for i in range(len(first_series))
                    if any(series[i] >= self.min_value for series in self.datasets.values())
                ]
                if keep_indices:
                    self.labels = [self.labels[i] for i in keep_indices]
                    self.datasets = {
                        name: [values[i] for i in keep_indices]
                        for name, values in self.datasets.items()
                    }

    @discord.ui.button(label="📊 Graph", style=discord.ButtonStyle.primary)
    async def graph_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle graph button click."""
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "This chart is not for you.", ephemeral=True
            )

        await interaction.response.defer()

        try:
            buffer = self._render_chart()
            file = discord.File(buffer, filename="metrics.png")
            await interaction.followup.send(file=file)
        except Exception as e:
            await interaction.followup.send(f"Failed to render chart: {e}", ephemeral=True)

    def _render_chart(self) -> io.BytesIO:
        """Render the appropriate chart type.

        Returns:
            BytesIO buffer containing the chart image.
        """
        if self.chart_type == "line":
            return render_line_chart(
                labels=self.labels,
                values=self.values,
                title=self.title,
                ylabel=self.ylabel,
            )
        elif self.chart_type == "bar":
            return render_bar_chart(
                labels=self.labels,
                values=self.values,
                title=self.title,
                ylabel=self.ylabel,
                horizontal=True,
                limit=self.limit,
            )
        elif self.chart_type == "pie":
            return render_pie_chart(
                labels=self.labels,
                values=self.values,
                title=self.title,
            )
        elif self.chart_type == "stacked":
            if not self.datasets:
                raise ValueError("Stacked chart requires datasets")
            return render_stacked_bar_chart(
                labels=self.labels,
                datasets=self.datasets,
                title=self.title,
                ylabel=self.ylabel,
            )
        elif self.chart_type == "multi":
            if not self.datasets:
                raise ValueError("Multi-line chart requires datasets")
            return render_multi_line_chart(
                labels=self.labels,
                datasets=self.datasets,
                title=self.title,
                ylabel=self.ylabel,
            )
        else:
            raise ValueError(f"Unknown chart type: {self.chart_type}")
