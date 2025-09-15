import { Modal, Input, message } from "antd";
import { setLocalStorage } from "./utils";
import { appContext } from "../hooks/provider";
import * as React from "react";
import { Button } from "./common/Button";
import { useTranslation } from 'react-i18next';

type SignInModalProps = {
  isVisible: boolean;
  onClose: () => void;
};

const SignInModal = ({ isVisible, onClose }: SignInModalProps) => {
  const { t, i18n } = useTranslation();
  const { user, setUser } = React.useContext(appContext);
  const [email, setEmail] = React.useState(user?.email || "default");

  const isAlreadySignedIn = !!user?.email;

  const handleEmailChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setEmail(e.target.value);
  };

  const handleSignIn = () => {
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      message.error("Username cannot be empty");
      return;
    }
    setUser({ ...user, email: trimmedEmail, name: trimmedEmail });
    setLocalStorage("user_email", trimmedEmail);
    onClose();
  };

  return (
    <Modal
      open={isVisible}
      footer={null}
      closable={isAlreadySignedIn}
      maskClosable={isAlreadySignedIn}
      onCancel={isAlreadySignedIn ? onClose : undefined}
    >
      <span className="text-lg">
        {(t("Enter a username.'))}<br></br> {(t('A change of username will create a new profile."))}
      </span>
      <div className="mb-4">
        <Input
          type="text"
          placeholder={(t("Enter a username."))}
          value={email}
          onChange={handleEmailChange}
          className="shadow-sm"
        />
      </div>
      <div className="flex justify-center">
        <Button
          variant="primary"
          onClick={handleSignIn}
        >
          Sign In
        </Button>
      </div>
    </Modal>
  );
};

export default SignInModal;
