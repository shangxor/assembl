// @flow
export type I18nValue = { [string]: string };

export type FileValue = ?{
  externalUrl: string,
  mimeType: string,
  title: string
};

export type MutationsPromises = Array<() => Promise<*>>;

export type SaveStatus = 'OK' | 'KO' | 'PENDING';