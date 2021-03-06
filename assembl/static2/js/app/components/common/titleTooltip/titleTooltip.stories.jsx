// @flow
import React from 'react';
/* eslint-disable import/no-extraneous-dependencies */
import { storiesOf } from '@storybook/react';
import { withKnobs } from '@storybook/addon-knobs';
/* eslint-enable */

import TitleTooltip from './titleTooltip';
import type { Props as TitleTooltipProps } from './titleTooltip';

export const defaultTitleTooltipProps: TitleTooltipProps = {
  tooltipContent: <p>Wow a tooltip!</p>
};

storiesOf('Semantic Analysis|TitleTooltip', module)
  .addDecorator(withKnobs)
  .add('default', () => <TitleTooltip {...defaultTitleTooltipProps} />);