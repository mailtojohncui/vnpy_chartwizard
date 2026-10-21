"""K 线图表界面组件。"""
from copy import copy
from datetime import datetime, timedelta
from typing import cast
from tzlocal import get_localzone_name

from vnpy.event import EventEngine, Event
from vnpy.chart import ChartWidget, CandleItem, VolumeItem
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import QtWidgets, QtCore
from vnpy.trader.event import EVENT_TICK, EVENT_CONTRACT
from vnpy.trader.object import ContractData, TickData, BarData, SubscribeRequest
from vnpy.trader.utility import BarGenerator, ZoneInfo, extract_vt_symbol
from vnpy.trader.constant import Interval, Exchange
from vnpy_spreadtrading.base import SpreadItem, EVENT_SPREAD_DATA

from ..engine import APP_NAME, EVENT_CHART_HISTORY, ChartWizardEngine


class ChartWizardWidget(QtWidgets.QWidget):
    """K线图表控件"""

    signal_tick: QtCore.Signal = QtCore.Signal(Event)
    signal_spread: QtCore.Signal = QtCore.Signal(Event)
    signal_history: QtCore.Signal = QtCore.Signal(Event)
    signal_contract: QtCore.Signal = QtCore.Signal(Event)

    def __init__(self, main_engine: MainEngine, event_engine: EventEngine) -> None:
        """构造函数"""
        super().__init__()

        self.main_engine: MainEngine = main_engine
        self.event_engine: EventEngine = event_engine
        self.chart_engine: ChartWizardEngine = cast(ChartWizardEngine, main_engine.get_engine(APP_NAME))

        self.bgs: dict[str, BarGenerator] = {}
        self.charts: dict[str, ChartWidget] = {}
        self.pending: set[str] = set()      # 等待合约信息返回的代码（自定义扩展）

        self.init_ui()
        self.register_event()

    def init_ui(self) -> None:
        """初始化界面"""
        self.setWindowTitle("K线图表")

        self.tab: QtWidgets.QTabWidget = QtWidgets.QTabWidget()

        self.tab.setTabsClosable(True)
        self.tab.tabCloseRequested.connect(self.close_tab)

        self.symbol_line: QtWidgets.QLineEdit = QtWidgets.QLineEdit()

        self.button: QtWidgets.QPushButton = QtWidgets.QPushButton("新建图表")
        self.button.clicked.connect(self.new_chart)

        hbox: QtWidgets.QHBoxLayout = QtWidgets.QHBoxLayout()
        hbox.addWidget(QtWidgets.QLabel("本地代码"))
        hbox.addWidget(self.symbol_line)
        hbox.addWidget(self.button)
        hbox.addStretch()

        vbox: QtWidgets.QVBoxLayout = QtWidgets.QVBoxLayout()
        vbox.addLayout(hbox)
        vbox.addWidget(self.tab)

        self.setLayout(vbox)

    def create_chart(self) -> ChartWidget:
        """创建图表对象"""
        chart: ChartWidget = ChartWidget()
        chart.add_plot("candle", hide_x_axis=True)
        chart.add_plot("volume", maximum_height=200)
        chart.add_item(CandleItem, "candle", "candle")
        chart.add_item(VolumeItem, "volume", "volume")
        chart.add_cursor()
        return chart

    def show(self) -> None:
        """最大化显示"""
        self.showMaximized()

    def close_tab(self, index: int) -> None:
        """关闭标签"""
        vt_symbol: str = self.tab.tabText(index)

        self.tab.removeTab(index)
        self.charts.pop(vt_symbol)
        self.bgs.pop(vt_symbol)

    def new_chart(self) -> None:
        """创建新的图表"""
        vt_symbol: str = self.normalize_vt_symbol(self.symbol_line.text())
        if not vt_symbol:
            return

        self.symbol_line.setText(vt_symbol)
        self.open_chart(vt_symbol)

    def normalize_vt_symbol(self, text: str) -> str:
        """
        规范化输入的代码（自定义扩展）：
        期货简写代码自动转大写并补全交易所，例如 mes -> MES.CME
        """
        text = text.strip()
        if not text:
            return ""

        try:
            from vnpy_ib.future_alias import FUTURE_ALIASES
        except ImportError:
            return text

        if "." in text:
            symbol, exchange = text.rsplit(".", 1)
            if symbol.upper() in FUTURE_ALIASES:
                symbol = symbol.upper()
            return f"{symbol}.{exchange.upper()}"

        alias = FUTURE_ALIASES.get(text.upper(), None)
        if alias:
            return f"{text.upper()}.{alias.exchange}"

        return text

    def subscribe_pending(self, vt_symbol: str) -> None:
        """合约信息不存在时先订阅，合约返回后再创建图表（自定义扩展）"""
        try:
            symbol, exchange = extract_vt_symbol(vt_symbol)
        except ValueError:
            return

        gateway_names: list[str] = self.main_engine.get_all_gateway_names()
        if not gateway_names:
            return

        self.pending.add(vt_symbol)

        req: SubscribeRequest = SubscribeRequest(symbol, exchange)
        for gateway_name in gateway_names:
            self.main_engine.subscribe(req, gateway_name)

    def process_contract_event(self, event: Event) -> None:
        """合约信息返回后，创建等待中的图表（自定义扩展）"""
        contract: ContractData = event.data
        if contract.vt_symbol in self.pending:
            self.pending.discard(contract.vt_symbol)
            self.open_chart(contract.vt_symbol)

    def open_chart(self, vt_symbol: str) -> None:
        """打开指定代码的图表"""
        if vt_symbol in self.charts:
            return

        if "LOCAL" not in vt_symbol:
            contract: ContractData | None = self.main_engine.get_contract(vt_symbol)
            if not contract:
                self.subscribe_pending(vt_symbol)
                return

        # Create new chart
        self.bgs[vt_symbol] = BarGenerator(self.on_bar)

        chart: ChartWidget = self.create_chart()
        self.charts[vt_symbol] = chart

        self.tab.addTab(chart, vt_symbol)

        # Query history data
        end: datetime = datetime.now(ZoneInfo(get_localzone_name()))
        start: datetime = end - timedelta(days=5)

        self.chart_engine.query_history(
            vt_symbol,
            Interval.MINUTE,
            start,
            end
        )

    def register_event(self) -> None:
        """注册事件监听"""
        self.signal_tick.connect(self.process_tick_event)
        self.signal_history.connect(self.process_history_event)
        self.signal_spread.connect(self.process_spread_event)
        self.signal_contract.connect(self.process_contract_event)

        self.event_engine.register(EVENT_CHART_HISTORY, self.signal_history.emit)
        self.event_engine.register(EVENT_TICK, self.signal_tick.emit)
        self.event_engine.register(EVENT_SPREAD_DATA, self.signal_spread.emit)
        self.event_engine.register(EVENT_CONTRACT, self.signal_contract.emit)

    def process_tick_event(self, event: Event) -> None:
        """处理Tick事件"""
        tick: TickData = event.data
        bg: BarGenerator | None = self.bgs.get(tick.vt_symbol, None)

        if bg:
            bg.update_tick(tick)

            # 尚未收到有效成交价时K线还没生成，跳过（修复NoneType报错）
            if not bg.bar:
                return

            chart: ChartWidget = self.charts[tick.vt_symbol]
            bar: BarData = cast(BarData, copy(bg.bar))
            bar.datetime = bar.datetime.replace(second=0, microsecond=0)
            chart.update_bar(bar)

    def process_history_event(self, event: Event) -> None:
        """处理历史事件"""
        history: list[BarData] = event.data
        if not history:
            return

        bar: BarData = history[0]
        chart: ChartWidget = self.charts[bar.vt_symbol]
        chart.update_history(history)

        # Subscribe following data update
        contract: ContractData | None = self.main_engine.get_contract(bar.vt_symbol)
        if contract:
            req: SubscribeRequest = SubscribeRequest(
                contract.symbol,
                contract.exchange
            )
            self.main_engine.subscribe(req, contract.gateway_name)

    def process_spread_event(self, event: Event) -> None:
        """处理价差事件"""
        spread_item: SpreadItem = event.data
        tick: TickData = TickData(
            symbol=spread_item.name,
            exchange=Exchange.LOCAL,
            datetime=spread_item.datetime,
            name=spread_item.name,
            last_price=(spread_item.bid_price + spread_item.ask_price) / 2,
            bid_price_1=spread_item.bid_price,
            ask_price_1=spread_item.ask_price,
            bid_volume_1=spread_item.bid_volume,
            ask_volume_1=spread_item.ask_volume,
            gateway_name="SPREAD"
        )

        bg: BarGenerator | None = self.bgs.get(tick.vt_symbol, None)
        if bg:
            bg.update_tick(tick)

            # 尚未收到有效成交价时K线还没生成，跳过（修复NoneType报错）
            if not bg.bar:
                return

            chart: ChartWidget = self.charts[tick.vt_symbol]
            bar: BarData = cast(BarData, copy(bg.bar))
            bar.datetime = bar.datetime.replace(second=0, microsecond=0)
            chart.update_bar(bar)

    def on_bar(self, bar: BarData) -> None:
        """K线合成回调"""
        chart: ChartWidget = self.charts[bar.vt_symbol]
        chart.update_bar(bar)
