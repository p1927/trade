"""News analyst with trade-stack company research enrichment."""

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_global_news,
    get_instrument_context_from_state,
    get_language_instruction,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
)
from trade_integrations.tools.company_research_tools import get_company_research
from trade_integrations.tools.index_research_tools import get_index_research
from trade_integrations.tools.options_research_tools import get_options_research
from trade_integrations.tools.stock_research_tools import get_stock_research


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = get_instrument_context_from_state(state)

        tools = [
            get_news,
            get_global_news,
            get_macro_indicators,
            get_prediction_markets,
            get_company_research,
            get_index_research,
            get_options_research,
            get_stock_research,
        ]

        system_message = (
            f"You are a news researcher tasked with analyzing recent news and trends over the past week. "
            f"Please write a comprehensive report of the current state of the world that is relevant for "
            f"trading and macroeconomics. Use the available tools: "
            f"get_news(ticker, start_date, end_date) for {asset_label}-specific news by ticker symbol, "
            f"get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news, "
            f"get_macro_indicators(indicator, curr_date, look_back_days) to ground macro commentary in "
            f"actual data from FRED (e.g. 'cpi', 'core_pce', 'unemployment', 'fed_funds_rate', "
            f"'10y_treasury', 'yield_curve'), "
            f"get_prediction_markets(topic, limit) for live market-implied probabilities of forward-looking "
            f"events (e.g. 'Fed rate cut', 'recession 2026', geopolitical or sector events), "
            f"get_company_research(ticker, lookahead_days) for a structured dossier with company identity, "
            f"upcoming earnings/board meetings, and live Indian market context when configured, "
            f"get_index_research(ticker, horizon_days) for index-level research on NIFTY, BANKNIFTY, "
            f"and other NSE indices (prediction range, factor attribution, macro overlay, scenarios), "
            f"get_options_research(ticker, expiry_date, lookahead_days) for F&O trade plans on NIFTY, "
            f"BANKNIFTY, or stock underlyings (ranked strategies, payoff, charges), and "
            f"get_stock_research(ticker, lookahead_days) for equity trade plans (ranked BUY/HOLD "
            f"approaches, entry/target/stop, CNC execution steps). "
            f"For individual stocks, call get_company_research early to anchor event risk and identity, "
            f"then get_stock_research for the actionable equity plan (or get_options_research if the "
            f"run is options-focused on that name). "
            f"For index runs (NIFTY, BANKNIFTY), call get_index_research first for the quantitative "
            f"index view (factor contributors, range forecast, regime), then get_options_research for "
            f"actionable F&O strategies when the run is options-focused. "
            f"Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node
