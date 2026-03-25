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
}

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


def create_figure(figsize: tuple[int, int] = (10, 6)) -> tuple[Figure, Axes]:
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
    fig, ax = create_figure()
    
    # Convert date strings to datetime if possible
    x_values = range(len(labels))
    
    ax.plot(
        x_values,
        values,
        color=color,
        marker=marker,
        markersize=markersize,
        linewidth=2,
        label=title,
    )
    
    if fill:
        ax.fill_between(x_values, values, alpha=0.3, color=color)
    
    # Set x-axis labels
    ax.set_xticks(x_values)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    
    ax.set_xlabel("Date", color=CHART_STYLE["axes.labelcolor"])
    ax.set_ylabel(ylabel, color=CHART_STYLE["axes.labelcolor"])
    ax.set_title(title, fontsize=14, fontweight="bold", color=CHART_STYLE["text.color"])
    
    # Add grid
    ax.grid(True, alpha=CHART_STYLE["grid.alpha"], color=CHART_STYLE["grid.color"])
    
    # Add value annotations on hover points
    for i, (x, y) in enumerate(zip(x_values, values)):
        if i % max(1, len(values) // 10) == 0:  # Annotate every nth point
            ax.annotate(
                f"{y:,}",
                xy=(x, y),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                color=CHART_STYLE["text.color"],
            )
    
    plt.tight_layout()
    
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=100, bbox_inches="tight")
    buffer.seek(0)
    plt.close(fig)
    
    return buffer


def render_bar_chart(
    labels: list[str],
    values: list[int | float],
    title: str,
    ylabel: str,
    color: str = COLORS["primary"],
    horizontal: bool = False,
    show_values: bool = True,
) -> io.BytesIO:
    """Render a bar chart with improved styling.
    
    Args:
        labels: Bar labels.
        values: Bar values.
        title: Chart title.
        ylabel: Y-axis label.
        color: Bar color.
        horizontal: Whether to render horizontal bars.
        show_values: Whether to show value labels on bars.
        
    Returns:
        BytesIO buffer containing the PNG image.
    """
    fig, ax = create_figure()
    
    if horizontal:
        bars = ax.barh(labels, values, color=color, edgecolor="white", linewidth=0.5)
        ax.set_xlabel(ylabel, color=CHART_STYLE["axes.labelcolor"])
        
        if show_values:
            for bar, val in zip(bars, values):
                ax.text(
                    bar.get_width() + max(values) * 0.01,
                    bar.get_y() + bar.get_height() / 2,
                    f"{val:,}",
                    va="center",
                    fontsize=9,
                    color=CHART_STYLE["text.color"],
                )
    else:
        bars = ax.bar(labels, values, color=color, edgecolor="white", linewidth=0.5)
        ax.set_ylabel(ylabel, color=CHART_STYLE["axes.labelcolor"])
        
        # Rotate labels if too many
        if len(labels) > 10:
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
        
        if show_values:
            for bar, val in zip(bars, values):
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max(values) * 0.01,
                    f"{val:,}",
                    ha="center",
                    va="bottom",
                    fontsize=9,
                    color=CHART_STYLE["text.color"],
                )
    
    ax.set_title(title, fontsize=14, fontweight="bold", color=CHART_STYLE["text.color"])
    ax.grid(True, alpha=CHART_STYLE["grid.alpha"], color=CHART_STYLE["grid.color"], axis="y" if horizontal else "x")
    
    plt.tight_layout()
    
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=100, bbox_inches="tight")
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
    fig, ax = create_figure()
    
    if colors is None:
        color_list = list(COLORS.values())[:len(datasets)]
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
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_xlabel("Date", color=CHART_STYLE["axes.labelcolor"])
    ax.set_ylabel(ylabel, color=CHART_STYLE["axes.labelcolor"])
    ax.set_title(title, fontsize=14, fontweight="bold", color=CHART_STYLE["text.color"])
    ax.legend(loc="best", facecolor=CHART_STYLE["axes.facecolor"])
    ax.grid(True, alpha=CHART_STYLE["grid.alpha"], color=CHART_STYLE["grid.color"])
    
    plt.tight_layout()
    
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=100, bbox_inches="tight")
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
    
    colors = list(COLORS.values())[:len(labels)]
    
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
    
    ax.set_title(title, fontsize=14, fontweight="bold", color=CHART_STYLE["text.color"])
    
    plt.tight_layout()
    
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=100, bbox_inches="tight")
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
    fig, ax = create_figure()
    
    if colors is None:
        color_list = list(COLORS.values())[:len(datasets)]
    else:
        color_list = colors
    
    x_values = range(len(labels))
    bottom = [0] * len(labels)
    
    for (name, values), color in zip(datasets.items(), color_list):
        ax.bar(x_values, values, bottom=bottom, label=name, color=color)
        bottom = [b + v for b, v in zip(bottom, values)]
    
    ax.set_xticks(x_values)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_xlabel("Date", color=CHART_STYLE["axes.labelcolor"])
    ax.set_ylabel(ylabel, color=CHART_STYLE["axes.labelcolor"])
    ax.set_title(title, fontsize=14, fontweight="bold", color=CHART_STYLE["text.color"])
    ax.legend(loc="upper left", facecolor=CHART_STYLE["axes.facecolor"])
    ax.grid(True, alpha=CHART_STYLE["grid.alpha"], color=CHART_STYLE["grid.color"])
    
    plt.tight_layout()
    
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=100, bbox_inches="tight")
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