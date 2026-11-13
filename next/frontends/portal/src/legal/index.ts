import type { Lang } from '../i18n';
import { de } from './de';
import { en } from './en';
import { fr } from './fr';
import { it } from './it';
import type { LegalTexts } from './types';

export * from './types';

export const LEGAL: Record<Lang, LegalTexts> = { en, de, fr, it };
