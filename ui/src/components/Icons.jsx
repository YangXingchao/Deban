const I = ({ d, size = 18, ...p }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
       stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p}>
    {d}
  </svg>
)

export const IcHome = (p) => <I {...p} d={<><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9.5 21v-6h5v6"/></>} />
export const IcLive = (p) => <I {...p} d={<><circle cx="12" cy="12" r="2.6"/><path d="M6.3 6.3a8 8 0 0 0 0 11.4M17.7 6.3a8 8 0 0 1 0 11.4"/><path d="M3.5 3.5a12 12 0 0 0 0 17M20.5 3.5a12 12 0 0 1 0 17"/></>} />
export const IcBug = (p) => <I {...p} d={<><rect x="7" y="8" width="10" height="12" rx="5"/><path d="M12 8V5M9 5l-1.5-2M15 5l1.5-2M4 13h3M17 13h3M5 19l2.5-1.5M19 19l-2.5-1.5M5 8l2.5 1.5M19 8l-2.5 1.5"/></>} />
export const IcHistory = (p) => <I {...p} d={<><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></>} />
export const IcSettings = (p) => <I {...p} d={<><circle cx="12" cy="12" r="3.2"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.09a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.09a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1z"/></>} />
export const IcPlay = (p) => <I {...p} d={<path d="M7 4.5v15l13-7.5z" fill="currentColor" stroke="none"/>} />
export const IcPause = (p) => <I {...p} d={<><rect x="6" y="4.5" width="4" height="15" rx="1.2" fill="currentColor" stroke="none"/><rect x="14" y="4.5" width="4" height="15" rx="1.2" fill="currentColor" stroke="none"/></>} />
export const IcStop = (p) => <I {...p} d={<rect x="5.5" y="5.5" width="13" height="13" rx="2.5" fill="currentColor" stroke="none"/>} />
export const IcWave = (p) => <I {...p} d={<path d="M3 12h1.5M6.5 8v8M10 4.5v15M13.5 7v10M17 9.5v5M20.5 12H22" strokeWidth="2.2"/>} />
export const IcMic = (p) => <I {...p} d={<><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3"/></>} />
export const IcLanguage = (p) => <I {...p} d={<><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 3.8 5.5 3.8 9S14.5 18.5 12 21M12 3C9.5 5.5 8.2 8.5 8.2 12S9.5 18.5 12 21"/></>} />
export const IcTranslate = (p) => <I {...p} d={<><path d="M4 7h11M11 4l4 3-4 3M20 17H9M13 14l-4 3 4 3"/></>} />
export const IcDoc = (p) => <I {...p} d={<><path d="M6 2.5h8l4.5 4.5V21a.5.5 0 0 1-.5.5H6a.5.5 0 0 1-.5-.5V3a.5.5 0 0 1 .5-.5z"/><path d="M14 2.5V7h4.5M9 12.5h6M9 16h6"/></>} />
export const IcUpload = (p) => <I {...p} d={<><path d="M12 16V4.5M7 9l5-4.5L17 9"/><path d="M4.5 16v3.5a1 1 0 0 0 1 1h13a1 1 0 0 0 1-1V16"/></>} />
export const IcSearch = (p) => <I {...p} d={<><circle cx="10.5" cy="10.5" r="6.5"/><path d="m20 20-4.5-4.5"/></>} />
export const IcEdit = (p) => <I {...p} d={<><path d="M4 20h4.5L20 8.5a2.1 2.1 0 0 0-3-3L5.5 17z"/><path d="m14.5 8 2.5 2.5"/></>} />
export const IcTrash = (p) => <I {...p} d={<><path d="M4 6.5h16M9.5 3.5h5M7 6.5 8 21h8l1-14.5"/><path d="M10 10.5v6M14 10.5v6"/></>} />
export const IcDownload = (p) => <I {...p} d={<><path d="M12 4v11.5M7 11l5 4.5 5-4.5"/><path d="M4.5 16v3.5a1 1 0 0 0 1 1h13a1 1 0 0 0 1-1V16"/></>} />
export const IcCopy = (p) => <I {...p} d={<><rect x="9" y="9" width="11" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></>} />
export const IcSpark = (p) => <I {...p} d={<path d="M12 2.5 14 9l6.5 2-6.5 2-2 6.5L10 13l-6.5-2L10 9z"/>} />
export const IcX = (p) => <I {...p} d={<path d="M5 5l14 14M19 5 5 19"/>} />
export const IcUsers = (p) => <I {...p} d={<><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 5a3.5 3.5 0 0 1 0 7M17.5 13.5a6.5 6.5 0 0 1 4 6.5"/></>} />
export const IcMsg = (p) => <I {...p} d={<path d="M21 12a8.5 8.5 0 0 1-8.5 8.5 8.4 8.4 0 0 1-3.8-.9L3 21l1.4-5.7A8.5 8.5 0 1 1 21 12z"/>} />
export const IcBell = (p) => <I {...p} d={<><path d="M18 9a6 6 0 1 0-12 0c0 6-2.5 7-2.5 7h17S18 15 18 9z"/><path d="M10 20a2.2 2.2 0 0 0 4 0"/></>} />
export const IcClockMini = (p) => <I {...p} d={<><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/></>} />
export const IcChev = (p) => <I {...p} d={<path d="m6 9 6 6 6-6"/>} />
const THUMB = "M7 10v11H3.5A1.5 1.5 0 0 1 2 19.5v-8A1.5 1.5 0 0 1 3.5 10H7zm0 0 4.2-7.2c.5-.9 1.6-1.1 2.4-.5.6.4.9 1.1.7 1.8L13.2 9H19a2.5 2.5 0 0 1 2.4 3.1l-1.8 7A2.5 2.5 0 0 1 17.2 21H7"
export const IcThumbUp = ({ filled, ...p }) => <I {...p} fill={filled ? 'currentColor' : 'none'} d={<path d={THUMB} />} />
export const IcThumbDown = ({ filled, ...p }) => <I {...p} fill={filled ? 'currentColor' : 'none'} d={<path d={THUMB} transform="rotate(180 12 12)" />} />
export const IcPanelL = (p) => <I {...p} d={<><rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="M9.5 4.5v15"/></>} />
export const IcPanelR = (p) => <I {...p} d={<><rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="M14.5 4.5v15"/></>} />
