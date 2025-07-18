import React from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import DocumentIframe from "./document_iframe";
import { useTranslation } from "react-i18next";

interface DocumentModalProps {
  isOpen: boolean;
  onClose: () => void;
  docUrl?: string;
  title?: string;
}

const DocumentModal: React.FC<DocumentModalProps> = ({
  isOpen,
  onClose,
  docUrl,
  title,
}) => {
  const { t } = useTranslation();

  if (!isOpen) {
    return null;
  }

  const modalContent = (
    <div
      className={`fixed inset-0 flex items-center justify-center bg-black bg-opacity-75`}
      style={{ zIndex: 1000 }}
      onClick={onClose}
    >
      <div
        className="bg-tertiary w-[90vw] h-[90vh] max-w-[1200px] rounded-lg p-4 flex flex-col relative overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex justify-between items-center pb-3 mb-2 border-b sticky top-0 bg-white z-20">
          <h2 className="text-xl font-semibold truncate pr-2">{t(title)}</h2>
          <button
            onClick={onClose}
            className="p-1 hover:bg-gray-100 rounded-full transition-colors"
            aria-label="Close document view"
            title={t("Close") as string}
          >
            <X size={20} />
          </button>
        </div>
        <div className="flex-grow p-2 h-full overflow-hidden">
          <DocumentIframe docUrl={docUrl} className="w-full flex-1" />
        </div>
      </div>
    </div>
  );

  return createPortal(modalContent, document.body);
};

export default DocumentModal;
