// @flow

import type { ApolloClient } from 'react-apollo';
import {
  createSave,
  convertToEntries,
  convertRichTextToEntries,
  getFileVariable,
  convertDateTimeToISO8601String
} from '../../../form/utils';
import type { DatePickerValue } from './types.flow';
import updateDiscussion from '../../../../graphql/mutations/updateDiscussion.graphql';

type UpdateDiscussion = updateDiscussion;

export const save = createSave('administration.landingPage.successSave');

const createVariablesFromValues = (values: DatePickerValue): UpdateDiscussion => ({
  titleEntries: values.headerTitle ? convertToEntries(values.headerTitle) : null,
  subtitleEntries: values.headerSubtitle ? convertRichTextToEntries(values.headerSubtitle) : null,
  buttonLabelEntries: values.headerButtonLabel ? convertToEntries(values.headerButtonLabel) : null,
  headerImage: getFileVariable(values.headerImage),
  logoImage: getFileVariable(values.headerLogoImage),
  startDate: convertDateTimeToISO8601String(values.headerStartDate),
  endDate: convertDateTimeToISO8601String(values.headerEndDate)
});

export const createMutationsPromises = (client: ApolloClient) => (values: UpdateDiscussion): Array<() => Promise<*>> => [
  () => {
    return client.mutate({
      mutation: updateDiscussion,
      variables: createVariablesFromValues(values)
    });
  }
];
