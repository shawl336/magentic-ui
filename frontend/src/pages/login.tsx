import * as React from "react";
import { graphql } from "gatsby";
import Login from "../components/auth/Login";
import { appContext } from "../hooks/provider";

const LoginPage = ({ data }: any) => {
  return (
    <div style={{ minHeight: "100vh" }}>
      <Login />
    </div>
  );
};

export default LoginPage;

export const query = graphql`
  query LoginPageQuery {
    site {
      siteMetadata {
        description
        title
      }
    }
  }
`;

