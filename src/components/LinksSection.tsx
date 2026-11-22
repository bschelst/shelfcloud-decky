// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org

import { ButtonItem, PanelSectionRow } from "@decky/ui";
import { FC } from "react";
import { FaGithub, FaGlobe, FaLink, FaQuestionCircle } from "react-icons/fa";

import { Collapsible, IconLabel } from "./Collapsible";

interface LinksSectionProps {
  gatewayUrl: string;
  githubUrl: string;
  openInBrowser: (url: string) => void;
}

export const LinksSection: FC<LinksSectionProps> = ({
  gatewayUrl, githubUrl, openInBrowser,
}) => (
  <Collapsible title="Links" icon={<FaLink size={14} />}>
    <PanelSectionRow>
      <ButtonItem
        layout="below"
        onClick={() => openInBrowser(`${gatewayUrl}/guide`)}
      >
        <IconLabel icon={<FaQuestionCircle size={14} />}>
          Help &amp; documentation
        </IconLabel>
      </ButtonItem>
    </PanelSectionRow>

    <PanelSectionRow>
      <ButtonItem layout="below" onClick={() => openInBrowser(gatewayUrl)}>
        <IconLabel icon={<FaGlobe size={14} />}>Shelfcloud website</IconLabel>
      </ButtonItem>
    </PanelSectionRow>

    <PanelSectionRow>
      <ButtonItem layout="below" onClick={() => openInBrowser(githubUrl)}>
        <IconLabel icon={<FaGithub size={14} />}>GitHub repository</IconLabel>
      </ButtonItem>
    </PanelSectionRow>
  </Collapsible>
);
