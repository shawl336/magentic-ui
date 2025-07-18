import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from './locales/en/en.json';
import zh from './locales/zh/zh.json';


i18n
  .use(initReactI18next) // load translations from files
  .init({
    resources: {
      en: { translation: en },
      zh: { translation: zh }
    },
    fallbackLng: 'zh',
    interpolation: {
      escapeValue: false
    }
  });

export default i18n;
