/** The types shared by the reference tables. */
export type Lang = 'pt' | 'en'
export type SpecId = 'cell' | 'control'
export type Method = 'get' | 'post' | 'put' | 'patch' | 'delete'
export type L = Record<Lang, string>
export type ApiPage = { kind: 'api'; slug: string; spec: SpecId; method: Method; path: string; title: L; description: L }
export type Group = { label: L; pages: ApiPage[] }
