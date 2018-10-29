// @flow
import React from 'react';
import { configure, shallow, mount } from 'enzyme';
import Adapter from 'enzyme-adapter-react-16';

import { DumbTopPostForm, getClassNames, submittingState } from '../../../../../js/app/components/debate/common/topPostForm';

configure({ adapter: new Adapter() });

describe('TopPostForm component', () => {
  const props = {
    contentLocale: 'en',
    ideaId: '123456',
    messageClassifier: 'positive',
    scrollOffset: 200,
    fillBodyLabelMsgId: '654321',
    bodyPlaceholderMsgId: '678910',
    postSuccessMsgId: '019876',
    bodyMaxLength: 400,
    draftSuccessMsgId: '8765432',
    createPost: jest.fn(),
    refetchIdea: jest.fn(),
    uploadDocument: jest.fn(),
    onDisplayForm: jest.fn(),
    draftable: false,
    ideaOnColumn: false
  };

  it('should expand the component', () => {
    const wrapper = shallow(<DumbTopPostForm {...props} />);
    const instance = wrapper.instance();
    instance.displayForm(true);
    const isActive = wrapper.state().isActive;
    expect(isActive).toBe(true);
  });

  it('should collapse the component', () => {
    const wrapper = shallow(<DumbTopPostForm {...props} />);
    wrapper.setState({ isActive: true });
    const instance = wrapper.instance();
    instance.resetForm(false);
    const isActive = wrapper.state().isActive;
    expect(isActive).toBe(false);
  });

  it('should render the title input when the component is not on multi column view', () => {
    const wrapper = mount(<DumbTopPostForm {...props} />);
    expect(wrapper.find('input[name="top-post-title"]')).toHaveLength(1);
  });

  it('should not render the title input when the component is on multi column view', () => {
    const wrapper = mount(<DumbTopPostForm {...props} />);
    wrapper.setProps({ ideaOnColumn: true });
    expect(wrapper.find('input[name="top-post-title"]')).toHaveLength(0);
  });

  it('should not render buttons when the component is collapsed', () => {
    const wrapper = shallow(<DumbTopPostForm {...props} />);
    wrapper.setState({ isActive: false });
    expect(wrapper.find('Button')).toHaveLength(0);
  });

  it('should render 2 buttons when the component is expanded but not draftable', () => {
    const wrapper = shallow(<DumbTopPostForm {...props} />);
    wrapper.setState({ isActive: true });
    expect(wrapper.find('Button')).toHaveLength(2);
  });

  it('should render 3 buttons when the component is expanded and draftable', () => {
    const wrapper = shallow(<DumbTopPostForm {...props} />);
    wrapper.setState({ isActive: true });
    wrapper.setProps({ draftable: true });
    expect(wrapper.find('Button')).toHaveLength(3);
  });

  it('should not render the rich text editor when the component is collapse', () => {
    const wrapper = mount(<DumbTopPostForm {...props} />);
    wrapper.setState({ isActive: false });
    expect(wrapper.find('.rich-text-editor')).toHaveLength(0);
  });

  it('should update the body with the new value', () => {
    const wrapper = shallow(<DumbTopPostForm {...props} />);
    const instance = wrapper.instance();
    instance.updateBody({ foo: 'bar' });
    const body = wrapper.state().body;
    expect(body).toEqual({ foo: 'bar' });
  });

  it('should update the subject with the new value', () => {
    const wrapper = shallow(<DumbTopPostForm {...props} />);
    const instance = wrapper.instance();
    instance.handleSubjectChange({ target: { value: 'Hello' } });
    const subject = wrapper.state().subject;
    expect(subject).toEqual('Hello');
  });

  it('should return the button class names when the multi-column view is active', () => {
    const buttonClasses = getClassNames(true, false);
    const expectedResult = 'button-submit button-dark btn btn-default right margin-m';
    expect(buttonClasses).toBe(expectedResult);
  });

  it('should return the button class names when the submitting mode is active', () => {
    const result = getClassNames(false, true);
    const expectedResult = 'button-submit button-dark btn btn-default right margin-l cursor-wait';
    expect(result).toBe(expectedResult);
  });

  it('should return a submitting state', () => {
    const result = submittingState(true);
    expect(result).toEqual({ submitting: true });
  });
});