"""Small visual layer for the local Streamlit analysis workspace."""

import html

import streamlit as st


def apply_theme(theme: str = "Light") -> None:
    if theme not in {"Light", "Dark"}:
        theme = "Light"
    st.markdown(
        """<style>
        :root { --vlad-navy: #173548; --vlad-teal: #087f8c; }
        .stApp { background: #f5f8fa; }
        [data-testid="stSidebar"] { background: #eaf1f5; }
        [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
        h1, h2, h3 { color: var(--vlad-navy); }
        .block-container { max-width: 1700px; padding-top: 4.5rem; padding-bottom: 3rem; }
        [data-testid="stSidebar"] [role="radiogroup"] { gap: .35rem; }
        [data-testid="stSidebar"] label[data-baseweb="radio"] {
            padding: .6rem .7rem; border-radius: 8px; background: #f7fafb;
            border: 1px solid #dce7ed; margin: 0; }
        [data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) {
            background: #d5eef0; border-color: #087f8c; font-weight: 650; }
        .vlad-eyebrow { color: #087f8c; font-size: .76rem; letter-spacing: .13em;
                        font-weight: 750; text-transform: uppercase; margin-bottom: .4rem; }
        .vlad-heading { color: #173548; font-size: 2.2rem; font-weight: 720;
                        line-height: 1.16; margin: 0 0 .65rem; }
        .vlad-lead { color: #526b79; font-size: 1.04rem; max-width: 820px; }
        .vlad-hero { background: linear-gradient(115deg, #fff 0%, #eef7f7 100%);
                     border: 1px solid #dce7ed; border-radius: 16px;
                     padding: 1.65rem 1.9rem; margin-bottom: 1.6rem; }
        [data-testid="stMetric"] { background: white; border: 1px solid #dce7ed;
                                    border-radius: 12px; padding: .8rem 1rem; }
        [data-testid="stForm"] { background: #fff; border-color: #dce7ed;
                                   border-radius: 12px; }
        .stButton > button, .stDownloadButton > button { border-radius: 8px; }
        .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
            background: #087f8c; border-color: #087f8c; }
        .vlad-small { font-size: .84rem; color: #5e7581; }
        code { overflow-wrap: anywhere; }
        .st-key-wf_dwi_picker div:has(> [data-testid="stMultiSelectTagsContainer"]) { max-height: none; }
        .st-key-wf_dwi_picker [data-testid="stMultiSelectTagsContainer"] { flex-wrap: wrap; }
        .st-key-wf_dwi_picker :is([data-tag], [data-baseweb="tag"]) {
            flex: 0 0 calc(100% - .25rem); max-width: calc(100% - .25rem);
            box-sizing: border-box; margin-inline: 0 .25rem;
            height: auto; min-height: 1.5rem; padding-block: .15rem;
        }
        .st-key-wf_dwi_picker :is([data-tag], [data-baseweb="tag"]) > span:first-child {
            flex: 1; min-width: 0; max-width: none; white-space: normal; overflow-wrap: anywhere;
        }
        .st-key-wf_dwi_picker :is([data-tag], [data-baseweb="tag"]) > :nth-child(2) { flex-shrink: 0; }
        @media (max-width: 700px) {
          .vlad-hero { padding: 1.2rem; }
          .vlad-heading { font-size: 1.7rem; }
          .block-container { padding: 1rem; }
        }
        </style>""",
        unsafe_allow_html=True,
    )
    if theme == "Dark":
        st.markdown(
            """<style>
            :root { color-scheme: dark; --vlad-navy: #e6f4f5; --vlad-teal: #43c5ca; }
            .stApp, [data-testid="stAppViewContainer"] { background: #111827; color: #e5e7eb; }
            [data-testid="stHeader"] { background: rgba(17, 24, 39, .94); }
            [data-testid="stSidebar"] { background: #182235; }
            [data-testid="stSidebar"] label[data-baseweb="radio"] {
                color: #e5e7eb; background: #202c40; border-color: #3a4960; }
            [data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) {
                color: #f3ffff; background: #174c55; border-color: #43c5ca; }
            .stApp p, .stApp label, .stApp li, .stApp [data-testid="stCaptionContainer"] {
                color: #d2d9e4; }
            .vlad-heading, .vlad-eyebrow, h1, h2, h3 { color: #e6f4f5 !important; }
            .vlad-lead, .vlad-small { color: #b4c3cf; }
            .vlad-hero { background: linear-gradient(115deg, #1c2939 0%, #14333a 100%);
                         border-color: #35485a; }
            [data-testid="stMetric"], [data-testid="stForm"] {
                background: #1b2738; border-color: #35485a; }
            [data-baseweb="input"] > div, [data-baseweb="select"] > div,
            [data-baseweb="textarea"] > div, [data-testid="stPopoverBody"] {
                color: #edf2f7; background-color: #1d293a; border-color: #46566b; }
            [data-testid="stExpander"], [data-testid="stDataFrame"],
            [data-testid="stTable"] { border-color: #3a4960; background: #172235; }
            .stButton > button:not([kind="primary"]), .stDownloadButton > button {
                color: #e5e7eb; background: #202c40; border-color: #46566b; }
            code, pre { color: #e6edf3 !important; background: #0d1522 !important; }
            </style>""",
            unsafe_allow_html=True,
        )


def hero(page: str, cohort_name: str | None = None) -> None:
    copy = {
        "Overview": ("Your analysis workspace", "Set up a study, build its design, and follow an analysis from start to report."),
        "Study setup": ("Start with your study", "Register an existing cohort or create its settings, then prepare the data for analysis."),
        "Design": ("Build a clear study design", "Choose your spreadsheet and model variables. Preview the design before saving it."),
        "Run analysis": ("Run VLAD with confidence", "Choose the model and analysis settings, review readiness, then follow the live output."),
        "Reports": ("Explore your results", "View completed reports and download the figures, merged data, and statistical summaries."),
    }
    title, subtitle = copy[page]
    study = f" · {cohort_name}" if cohort_name else ""
    st.markdown(
        '<div class="vlad-hero">'
        f'<div class="vlad-eyebrow">VLAD{html.escape(study)}</div>'
        f'<h1 class="vlad-heading">{html.escape(title)}</h1>'
        f'<div class="vlad-lead">{html.escape(subtitle)}</div>'
        '</div>',
        unsafe_allow_html=True,
    )
